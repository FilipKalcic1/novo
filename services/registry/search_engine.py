"""
Search Engine - Semantic and keyword search for tool discovery.
Version: 1.0

Single responsibility: Find relevant tools using embeddings and scoring.
"""

import logging
import re
from typing import Dict, List, Set, Tuple, Any, Optional

from openai import AsyncAzureOpenAI

from config import get_settings
from services.tool_contracts import UnifiedToolDefinition, DependencyGraph
from services.scoring_utils import cosine_similarity
from services.patterns import (
    READ_INTENT_PATTERNS,
    MUTATION_INTENT_PATTERNS,
    USER_SPECIFIC_PATTERNS,
    USER_FILTER_PARAMS
)

logger = logging.getLogger(__name__)
settings = get_settings()


class SearchEngine:
    """
    Handles tool discovery through semantic and keyword search.

    Responsibilities:
    - Generate query embeddings
    - Calculate similarity scores
    - Apply intent-based scoring adjustments
    - Handle fallback keyword search
    """

    # Configuration for method disambiguation
    DISAMBIGUATION_CONFIG = {
        "read_intent_penalty_factor": 0.25,
        "unclear_intent_penalty_factor": 0.10,
        "delete_penalty_multiplier": 2.0,
        "update_penalty_multiplier": 1.0,
        "create_penalty_multiplier": 0.5,
        "get_boost_on_read_intent": 0.05,
    }

    MAX_TOOLS_PER_RESPONSE = 12

    def __init__(self):
        """Initialize search engine with OpenAI client."""
        self.openai = AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION
        )
        logger.debug("SearchEngine initialized")

    async def find_relevant_tools_with_scores(
        self,
        query: str,
        tools: Dict[str, UnifiedToolDefinition],
        embeddings: Dict[str, List[float]],
        dependency_graph: Dict[str, DependencyGraph],
        retrieval_tools: Set[str],
        mutation_tools: Set[str],
        top_k: int = 5,
        threshold: float = 0.55,
        prefer_retrieval: bool = False,
        prefer_mutation: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Find relevant tools with similarity scores.

        Args:
            query: User query
            tools: Dict of all tools
            embeddings: Dict of embeddings by operation_id
            dependency_graph: Dependency graph for chaining
            retrieval_tools: Set of retrieval tool IDs
            mutation_tools: Set of mutation tool IDs
            top_k: Number of base tools to return
            threshold: Minimum similarity threshold
            prefer_retrieval: Only search retrieval tools
            prefer_mutation: Only search mutation tools

        Returns:
            List of dicts with name, score, and schema
        """
        query_embedding = await self._get_query_embedding(query)

        if not query_embedding:
            fallback = self._fallback_keyword_search(query, tools, top_k)
            return [
                {"name": name, "score": 0.0, "schema": tools[name].to_openai_function()}
                for name in fallback
            ]

        # Determine search pool
        search_pool = set(tools.keys())
        if prefer_retrieval:
            search_pool = retrieval_tools
        elif prefer_mutation:
            search_pool = mutation_tools

        # Calculate similarity scores
        lenient_threshold = max(0.40, threshold - 0.20)
        scored = []

        for op_id in search_pool:
            if op_id not in embeddings:
                continue

            similarity = cosine_similarity(query_embedding, embeddings[op_id])

            if similarity >= lenient_threshold:
                scored.append((similarity, op_id))

        # Apply scoring adjustments
        scored = self._apply_method_disambiguation(query, scored, tools)
        scored = self._apply_user_specific_boosting(query, scored, tools)
        scored = self._apply_evaluation_adjustment(scored)

        scored.sort(key=lambda x: x[0], reverse=True)

        # Expansion search if needed
        if len(scored) < top_k and len(scored) > 0:
            keyword_matches = self._description_keyword_search(query, search_pool, tools)
            for op_id, desc_score in keyword_matches:
                if op_id not in [s[1] for s in scored]:
                    scored.append((desc_score * 0.7, op_id))
            scored.sort(key=lambda x: x[0], reverse=True)

        if not scored:
            fallback = self._fallback_keyword_search(query, tools, top_k)
            return [
                {"name": name, "score": 0.0, "schema": tools[name].to_openai_function()}
                for name in fallback
            ]

        # Apply dependency boosting
        base_tools = [op_id for _, op_id in scored[:top_k]]
        boosted_tools = self._apply_dependency_boosting(base_tools, dependency_graph)
        final_tools = boosted_tools[:self.MAX_TOOLS_PER_RESPONSE]

        # Build result
        result = []
        scored_dict = {op_id: score for score, op_id in scored}

        for tool_id in final_tools:
            schema = tools[tool_id].to_openai_function()
            score = scored_dict.get(tool_id, 0.0)
            result.append({
                "name": tool_id,
                "score": score,
                "schema": schema
            })

        logger.info(f"📦 Returning {len(result)} tools with scores")
        return result

    async def _get_query_embedding(self, query: str) -> Optional[List[float]]:
        """Get embedding for query text."""
        try:
            response = await self.openai.embeddings.create(
                input=[query[:8000]],
                model=settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
            )
            return response.data[0].embedding
        except Exception as e:
            logger.warning(f"Query embedding error: {e}")
            return None

    def _apply_method_disambiguation(
        self,
        query: str,
        scored: List[Tuple[float, str]],
        tools: Dict[str, UnifiedToolDefinition]
    ) -> List[Tuple[float, str]]:
        """Apply penalties/boosts based on detected intent."""
        query_lower = query.lower().strip()

        is_read_intent = any(
            re.search(pattern, query_lower)
            for pattern in READ_INTENT_PATTERNS
        )

        is_mutation_intent = any(
            re.search(pattern, query_lower)
            for pattern in MUTATION_INTENT_PATTERNS
        )

        if is_mutation_intent:
            return scored

        config = self.DISAMBIGUATION_CONFIG
        penalty_factor = (
            config["read_intent_penalty_factor"] if is_read_intent
            else config["unclear_intent_penalty_factor"]
        )

        if is_read_intent:
            logger.info(f"🔍 Detected READ intent in: '{query}'")

        adjusted = []
        for score, op_id in scored:
            tool = tools.get(op_id)
            if not tool:
                adjusted.append((score, op_id))
                continue

            op_id_lower = op_id.lower()

            if tool.method == "DELETE" or "delete" in op_id_lower:
                penalty = penalty_factor * config["delete_penalty_multiplier"]
                new_score = max(0, score - penalty)
                adjusted.append((new_score, op_id))

            elif tool.method in ("PUT", "PATCH"):
                penalty = penalty_factor * config["update_penalty_multiplier"]
                new_score = max(0, score - penalty)
                adjusted.append((new_score, op_id))

            elif tool.method == "POST" and "get" not in op_id_lower:
                is_search_post = any(x in op_id_lower for x in ["search", "query", "find", "filter"])
                if is_search_post:
                    adjusted.append((score, op_id))
                else:
                    penalty = penalty_factor * config["create_penalty_multiplier"]
                    new_score = max(0, score - penalty)
                    adjusted.append((new_score, op_id))

            elif tool.method == "GET" and is_read_intent:
                boost = config["get_boost_on_read_intent"]
                adjusted.append((score + boost, op_id))

            else:
                adjusted.append((score, op_id))

        return adjusted

    def _apply_user_specific_boosting(
        self,
        query: str,
        scored: List[Tuple[float, str]],
        tools: Dict[str, UnifiedToolDefinition]
    ) -> List[Tuple[float, str]]:
        """Boost tools that support user-specific filtering."""
        query_lower = query.lower().strip()

        is_user_specific = any(
            re.search(pattern, query_lower)
            for pattern in USER_SPECIFIC_PATTERNS
        )

        if not is_user_specific:
            return scored

        logger.info(f"👤 Detected USER-SPECIFIC intent in: '{query}'")

        boost_value = 0.15
        penalty_value = 0.10
        masterdata_boost = 0.25
        calendar_penalty = 0.20

        adjusted = []
        for score, op_id in scored:
            tool = tools.get(op_id)
            if not tool:
                adjusted.append((score, op_id))
                continue

            tool_params_lower = {p.lower() for p in tool.parameters.keys()}
            has_user_filter = bool(tool_params_lower & USER_FILTER_PARAMS)
            op_id_lower = op_id.lower()

            if 'masterdata' in op_id_lower:
                new_score = score + masterdata_boost + boost_value
                adjusted.append((new_score, op_id))

            elif 'calendar' in op_id_lower or 'equipment' in op_id_lower:
                new_score = max(0, score - calendar_penalty)
                adjusted.append((new_score, op_id))

            elif has_user_filter:
                new_score = score + boost_value
                adjusted.append((new_score, op_id))

            elif tool.method == "GET" and 'vehicle' in op_id_lower:
                new_score = max(0, score - penalty_value)
                adjusted.append((new_score, op_id))

            else:
                adjusted.append((score, op_id))

        return adjusted

    def _apply_evaluation_adjustment(
        self,
        scored: List[Tuple[float, str]]
    ) -> List[Tuple[float, str]]:
        """Apply performance-based adjustments."""
        try:
            from services.tool_evaluator import get_tool_evaluator
            evaluator = get_tool_evaluator()

            adjusted = []
            for score, op_id in scored:
                new_score = evaluator.apply_evaluation_adjustment(op_id, score)
                adjusted.append((new_score, op_id))
            return adjusted
        except ImportError:
            return scored

    def _apply_dependency_boosting(
        self,
        base_tools: List[str],
        dependency_graph: Dict[str, DependencyGraph]
    ) -> List[str]:
        """Add provider tools for dependency chaining."""
        result = list(base_tools)

        for tool_id in base_tools:
            dep_graph = dependency_graph.get(tool_id)
            if not dep_graph:
                continue

            for provider_id in dep_graph.provider_tools[:2]:
                if provider_id not in result:
                    result.append(provider_id)
                    logger.debug(f"🔗 Boosted {provider_id} for {tool_id}")

        return result

    def _description_keyword_search(
        self,
        query: str,
        search_pool: Set[str],
        tools: Dict[str, UnifiedToolDefinition],
        max_results: int = 5
    ) -> List[Tuple[str, float]]:
        """Search tool descriptions for keyword matches."""
        query_lower = query.lower()
        query_words = set(query_lower.split())

        matches = []

        for op_id in search_pool:
            tool = tools.get(op_id)
            if not tool:
                continue

            param_text = " ".join([
                p.name.lower() + " " + p.description.lower()
                for p in tool.parameters.values()
            ])
            output_text = " ".join(tool.output_keys).lower()

            searchable_text = " ".join([
                tool.description.lower(),
                tool.summary.lower(),
                tool.operation_id.lower(),
                " ".join(tool.tags).lower(),
                param_text,
                output_text
            ])

            score = 0.0
            searchable_words = set(searchable_text.split())
            word_overlap = len(query_words & searchable_words)
            score += word_overlap * 0.5

            for q_word in query_words:
                if len(q_word) >= 4 and q_word in searchable_text:
                    score += 0.3

            if score > 0:
                matches.append((op_id, score))

        matches.sort(key=lambda x: x[1], reverse=True)
        return matches[:max_results]

    def _fallback_keyword_search(
        self,
        query: str,
        tools: Dict[str, UnifiedToolDefinition],
        top_k: int
    ) -> List[str]:
        """Fallback keyword search when embeddings fail."""
        query_lower = query.lower()
        matches = []

        for op_id, tool in tools.items():
            text = f"{tool.description} {tool.path}".lower()
            score = sum(1 for word in query_lower.split() if word in text)

            if score > 0:
                matches.append((score, op_id))

        matches.sort(key=lambda x: x[0], reverse=True)
        return [op_id for _, op_id in matches[:top_k]]
