"""
Tool Registry - Public API facade.
Version: 2.0 (Refactored)

This module provides backward-compatible interface to the refactored registry.
All existing imports from services.tool_registry should work unchanged.
"""

import asyncio
import logging
from typing import Dict, List, Any, Optional, Set

from services.tool_contracts import UnifiedToolDefinition, DependencyGraph

from .tool_store import ToolStore
from .cache_manager import CacheManager
from .swagger_parser import SwaggerParser
from .embedding_engine import EmbeddingEngine
from .search_engine import SearchEngine

logger = logging.getLogger(__name__)

# Re-export for backward compatibility
__all__ = ['ToolRegistry', 'ToolStore', 'CacheManager', 'SwaggerParser', 'EmbeddingEngine', 'SearchEngine']


class ToolRegistry:
    """
    Dynamic tool registry with 2-step discovery.

    Architecture:
    1. Load Swagger specs -> Parse -> Build UnifiedToolDefinition
    2. Generate embeddings for semantic search
    3. Build dependency graph for auto-chaining
    4. Persist to .cache/ for fast startup

    This is a facade that coordinates the refactored components.
    """

    MAX_TOOLS_PER_RESPONSE = 12

    def __init__(self, redis_client=None):
        """Initialize tool registry with all components."""
        self.redis = redis_client

        # Initialize components
        self._store = ToolStore()
        self._cache = CacheManager()
        self._parser = SwaggerParser()
        self._embedding = EmbeddingEngine()
        self._search = SearchEngine()

        # State
        self.is_ready = False
        self._load_lock = asyncio.Lock()

        logger.info("ToolRegistry initialized (v3.0 - filter-then-search)")

    # ═══════════════════════════════════════════════
    # BACKWARD COMPATIBILITY PROPERTIES
    # ═══════════════════════════════════════════════

    @property
    def tools(self) -> Dict[str, UnifiedToolDefinition]:
        """Access tools dict for backward compatibility."""
        return self._store.tools

    @property
    def embeddings(self) -> Dict[str, List[float]]:
        """Access embeddings dict for backward compatibility."""
        return self._store.embeddings

    @property
    def dependency_graph(self) -> Dict[str, DependencyGraph]:
        """Access dependency graph for backward compatibility."""
        return self._store.dependency_graph

    @property
    def retrieval_tools(self) -> Set[str]:
        """Access retrieval tools set for backward compatibility."""
        return self._store.retrieval_tools

    @property
    def mutation_tools(self) -> Set[str]:
        """Access mutation tools set for backward compatibility."""
        return self._store.mutation_tools

    # Context param patterns (exposed for message_engine)
    @property
    def CONTEXT_PARAM_FALLBACK(self) -> Dict[str, str]:
        """Access context param fallback for backward compatibility."""
        return self._parser.context_param_fallback

    # ═══════════════════════════════════════════════
    # INITIALIZATION
    # ═══════════════════════════════════════════════

    async def load_swagger(self, source: str) -> bool:
        """Backward compatible method for single source."""
        logger.warning("⚠️ load_swagger() is DEPRECATED. Use initialize([sources]).")
        return await self.initialize([source])

    async def initialize(self, swagger_sources: List[str]) -> bool:
        """
        Initialize registry from Swagger sources.

        Args:
            swagger_sources: List of swagger.json URLs

        Returns:
            True if successful
        """
        async with self._load_lock:
            try:
                # Check cache validity
                if await self._cache.is_cache_valid(swagger_sources):
                    logger.info("✅ Cache valid - loading from disk")
                    cached_data = await self._cache.load_cache()

                    # Populate store
                    for tool in cached_data["tools"]:
                        self._store.add_tool(tool)

                    for dep in cached_data["dependency_graph"]:
                        self._store.add_dependency(dep)

                    for op_id, embedding in cached_data["embeddings"].items():
                        self._store.add_embedding(op_id, embedding)

                    self.is_ready = True
                    logger.info(
                        f"✅ Loaded {self._store.count()} tools from cache "
                        f"({len(self._store.retrieval_tools)} retrieval, "
                        f"{len(self._store.mutation_tools)} mutation)"
                    )
                    return True

                logger.info("🔄 Cache invalid - fetching Swagger specs...")

                # Parse all swagger sources
                for source in swagger_sources:
                    tools = await self._parser.parse_spec(
                        source,
                        self._embedding.build_embedding_text
                    )
                    for tool in tools:
                        self._store.add_tool(tool)

                if self._store.count() == 0:
                    logger.error("❌ No tools loaded from Swagger sources")
                    return False

                logger.info(f"📦 Loaded {self._store.count()} tools from Swagger")

                # Generate embeddings
                new_embeddings = await self._embedding.generate_embeddings(
                    self._store.tools,
                    self._store.embeddings
                )
                for op_id, embedding in new_embeddings.items():
                    self._store.add_embedding(op_id, embedding)

                # Build dependency graph
                dep_graph = self._embedding.build_dependency_graph(self._store.tools)
                for dep in dep_graph.values():
                    self._store.add_dependency(dep)

                # Save cache
                await self._cache.save_cache(
                    swagger_sources,
                    list(self._store.tools.values()),
                    self._store.embeddings,
                    list(self._store.dependency_graph.values())
                )

                self.is_ready = True
                logger.info(
                    f"✅ Initialized {self._store.count()} tools "
                    f"({len(self._store.retrieval_tools)} retrieval, "
                    f"{len(self._store.mutation_tools)} mutation)"
                )
                return True

            except Exception as e:
                logger.error(f"❌ Initialization failed: {e}", exc_info=True)
                return False

    # ═══════════════════════════════════════════════
    # SEARCH & DISCOVERY
    # ═══════════════════════════════════════════════

    async def find_relevant_tools(
        self,
        query: str,
        top_k: int = 5,
        prefer_retrieval: bool = False,
        prefer_mutation: bool = False,
        threshold: float = None
    ) -> List[Dict[str, Any]]:
        """
        Find relevant tools (backward compatible).

        Returns list of OpenAI function schemas.
        """
        if not self.is_ready:
            logger.warning("Registry not ready")
            return []

        results = await self.find_relevant_tools_with_scores(
            query=query,
            top_k=top_k,
            threshold=threshold or 0.55,
            prefer_retrieval=prefer_retrieval,
            prefer_mutation=prefer_mutation
        )

        return [r["schema"] for r in results]

    async def find_relevant_tools_with_scores(
        self,
        query: str,
        top_k: int = 5,
        threshold: float = 0.55,
        prefer_retrieval: bool = False,
        prefer_mutation: bool = False,
        use_filtered_search: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Find relevant tools WITH SIMILARITY SCORES.

        Args:
            query: User query
            top_k: Number of tools to return
            threshold: Minimum similarity threshold
            prefer_retrieval: Only search GET methods
            prefer_mutation: Only search POST/PUT/DELETE methods
            use_filtered_search: Use FILTER-THEN-SEARCH approach (default True)

        Returns list of dicts with name, score, and schema.
        """
        if not self.is_ready:
            logger.warning("Registry not ready")
            return []

        # v3.0: FILTER-THEN-SEARCH - reduces search space for better accuracy
        if use_filtered_search:
            return await self._search.find_relevant_tools_filtered(
                query=query,
                tools=self._store.tools,
                embeddings=self._store.embeddings,
                dependency_graph=self._store.dependency_graph,
                retrieval_tools=self._store.retrieval_tools,
                mutation_tools=self._store.mutation_tools,
                top_k=top_k,
                threshold=threshold
            )

        # Fallback: Original search method
        return await self._search.find_relevant_tools_with_scores(
            query=query,
            tools=self._store.tools,
            embeddings=self._store.embeddings,
            dependency_graph=self._store.dependency_graph,
            retrieval_tools=self._store.retrieval_tools,
            mutation_tools=self._store.mutation_tools,
            top_k=top_k,
            threshold=threshold,
            prefer_retrieval=prefer_retrieval,
            prefer_mutation=prefer_mutation
        )

    # ═══════════════════════════════════════════════
    # TOOL ACCESS
    # ═══════════════════════════════════════════════

    def get_tool(self, operation_id: str) -> Optional[UnifiedToolDefinition]:
        """Get tool by operation ID."""
        return self._store.get_tool(operation_id)

    def list_tools(self) -> List[str]:
        """List all tool operation IDs."""
        return self._store.list_tools()
