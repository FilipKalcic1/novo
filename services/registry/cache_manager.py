"""
Cache Manager - Persistent caching for tool registry.
Version: 1.0

Single responsibility: Load and save tool data to .cache/ directory.
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from services.tool_contracts import UnifiedToolDefinition, DependencyGraph

logger = logging.getLogger(__name__)

# Cache version - increment when tool_categories.json or other config changes require cache rebuild
CACHE_VERSION = "2.2"  # v2.2: FORCE rebuild with enhanced booking descriptions

# Cache file paths
CACHE_DIR = Path.cwd() / ".cache"
EMBEDDINGS_CACHE_FILE = CACHE_DIR / "tool_embeddings.json"
METADATA_CACHE_FILE = CACHE_DIR / "tool_metadata.json"
MANIFEST_CACHE_FILE = CACHE_DIR / "swagger_manifest.json"


class CacheManager:
    """
    Manages persistent caching of tools and embeddings.

    Responsibilities:
    - Validate cache freshness
    - Load cached data
    - Save data to cache
    - Verify cache integrity
    """

    def __init__(self):
        """Initialize cache manager."""
        CACHE_DIR.mkdir(exist_ok=True)
        logger.debug(f"CacheManager initialized, dir: {CACHE_DIR}")

    async def is_cache_valid(self, swagger_sources: List[str]) -> bool:
        """
        Validate cache against swagger sources.

        Checks:
        1. All cache files exist
        2. Files are not empty
        3. Files are valid JSON
        4. Swagger sources match
        5. Tools and embeddings are present
        """
        # Check file existence
        if not MANIFEST_CACHE_FILE.exists():
            logger.debug("Cache invalid: manifest missing")
            return False

        if not METADATA_CACHE_FILE.exists():
            logger.debug("Cache invalid: metadata missing")
            return False

        if not EMBEDDINGS_CACHE_FILE.exists():
            logger.debug("Cache invalid: embeddings missing")
            return False

        # Check file sizes (not empty)
        if MANIFEST_CACHE_FILE.stat().st_size == 0:
            logger.warning("Cache corrupted: manifest empty")
            return False

        if METADATA_CACHE_FILE.stat().st_size == 0:
            logger.warning("Cache corrupted: metadata empty")
            return False

        if EMBEDDINGS_CACHE_FILE.stat().st_size == 0:
            logger.warning("Cache corrupted: embeddings empty")
            return False

        try:
            # Load and validate manifest
            manifest = await asyncio.to_thread(
                self._read_json_sync,
                MANIFEST_CACHE_FILE
            )

            if "swagger_sources" not in manifest:
                logger.warning("Cache corrupted: manifest missing swagger_sources")
                return False

            # Check cache version
            cached_version = manifest.get("cache_version")
            if cached_version != CACHE_VERSION:
                logger.info(f"Cache invalid: version mismatch (cached={cached_version}, current={CACHE_VERSION})")
                return False

            # Check if sources match
            cached_sources = set(manifest.get("swagger_sources", []))
            current_sources = set(swagger_sources)

            if cached_sources != current_sources:
                logger.info("Cache invalid: swagger sources changed")
                return False

            # Validate metadata structure
            metadata = await asyncio.to_thread(
                self._read_json_sync,
                METADATA_CACHE_FILE
            )

            if "tools" not in metadata or not isinstance(metadata["tools"], list):
                logger.warning("Cache corrupted: metadata missing tools array")
                return False

            if len(metadata["tools"]) == 0:
                logger.warning("Cache invalid: no tools in metadata")
                return False

            # Validate embeddings structure
            embeddings_data = await asyncio.to_thread(
                self._read_json_sync,
                EMBEDDINGS_CACHE_FILE
            )

            if "embeddings" not in embeddings_data:
                logger.warning("Cache corrupted: embeddings invalid structure")
                return False

            logger.info("✅ Cache valid - loading from disk")
            return True

        except json.JSONDecodeError as e:
            logger.warning(f"Cache corrupted: invalid JSON - {e}")
            return False
        except Exception as e:
            logger.warning(f"Cache validation failed: {e}")
            return False

    async def load_cache(self) -> Dict[str, Any]:
        """
        Load all cached data.

        Returns:
            Dict with 'tools', 'embeddings', 'dependency_graph'
        """
        try:
            # Load metadata
            metadata = await asyncio.to_thread(
                self._read_json_sync,
                METADATA_CACHE_FILE
            )

            tools = []
            for tool_dict in metadata.get("tools", []):
                tool = UnifiedToolDefinition(**tool_dict)
                tools.append(tool)

            dependency_graph = []
            for dep_dict in metadata.get("dependency_graph", []):
                dep = DependencyGraph(**dep_dict)
                dependency_graph.append(dep)

            logger.info(f"📦 Loaded {len(tools)} tools from cache")

            # Load embeddings
            embeddings_data = await asyncio.to_thread(
                self._read_json_sync,
                EMBEDDINGS_CACHE_FILE
            )
            embeddings = embeddings_data.get("embeddings", {})

            logger.info(f"📦 Loaded {len(embeddings)} embeddings from cache")

            return {
                "tools": tools,
                "embeddings": embeddings,
                "dependency_graph": dependency_graph
            }

        except Exception as e:
            logger.error(f"Cache load failed: {e}")
            raise

    async def save_cache(
        self,
        swagger_sources: List[str],
        tools: List[UnifiedToolDefinition],
        embeddings: Dict[str, List[float]],
        dependency_graph: List[DependencyGraph]
    ) -> None:
        """
        Save all data to cache.

        Args:
            swagger_sources: List of swagger URLs
            tools: List of tool definitions
            embeddings: Dict of embeddings by operation_id
            dependency_graph: List of dependency graphs
        """
        try:
            # Save manifest
            manifest = {
                "version": "2.0",
                "timestamp": datetime.utcnow().isoformat(),
                "cache_version": CACHE_VERSION,
                "swagger_sources": swagger_sources
            }
            await asyncio.to_thread(
                self._write_json_sync,
                MANIFEST_CACHE_FILE,
                manifest
            )
            logger.info(f"💾 Saved manifest: {len(swagger_sources)} sources")

            # Save metadata (mode='json' for Enum serialization)
            metadata = {
                "version": "2.0",
                "timestamp": datetime.utcnow().isoformat(),
                "tools": [
                    tool.model_dump(mode='json')
                    for tool in tools
                ],
                "dependency_graph": [
                    dep.model_dump(mode='json')
                    for dep in dependency_graph
                ]
            }
            await asyncio.to_thread(
                self._write_json_sync,
                METADATA_CACHE_FILE,
                metadata
            )
            logger.info(f"💾 Saved metadata: {len(tools)} tools")

            # Save embeddings
            embeddings_data = {
                "version": "2.0",
                "timestamp": datetime.utcnow().isoformat(),
                "embeddings": embeddings
            }
            await asyncio.to_thread(
                self._write_json_sync,
                EMBEDDINGS_CACHE_FILE,
                embeddings_data
            )
            logger.info(f"💾 Saved embeddings: {len(embeddings)} vectors")

            # Verify files were written
            await self._verify_cache_files()

            logger.info(
                f"✅ Cache save complete: "
                f"{len(tools)} tools, {len(embeddings)} embeddings"
            )

        except Exception as e:
            logger.error(f"❌ Cache save failed: {e}", exc_info=True)
            raise

    async def _verify_cache_files(self) -> None:
        """Verify all cache files were written correctly."""
        for cache_file, name in [
            (MANIFEST_CACHE_FILE, "manifest"),
            (METADATA_CACHE_FILE, "metadata"),
            (EMBEDDINGS_CACHE_FILE, "embeddings")
        ]:
            if not cache_file.exists():
                raise RuntimeError(f"Cache {name} not created: {cache_file}")

            size = cache_file.stat().st_size
            if size == 0:
                raise RuntimeError(f"Cache {name} is empty: {cache_file}")

            logger.info(f"✅ {name}: {cache_file.name} ({size:,} bytes)")

    def _read_json_sync(self, path: Path) -> Dict:
        """Synchronous JSON read."""
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_json_sync(self, path: Path, data: Dict) -> None:
        """Synchronous JSON write."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
