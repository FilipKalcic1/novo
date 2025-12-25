"""
Tool Registry - Dynamic Tool Discovery with Dependency Boosting
Version: 2.0

FAZA 1: Vector Search (Discovery) - Semantic similarity over embeddings
FAZA 2: Dependency Boosting (Logic Loop) - Auto-add provider tools

Key Features:
- Domain-agnostic metadata parsing
- 2-step tool discovery (max 12 tools to LLM)
- Persistent caching (startup < 1s)
- Dependency graph construction

NO hardcoded business logic.
"""

import asyncio
import hashlib
import json
import logging
import math
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional, Set, Tuple
from urllib.parse import urlparse

import httpx
from openai import AsyncAzureOpenAI

from config import get_settings
from services.tool_contracts import (
    UnifiedToolDefinition,
    ParameterDefinition,
    DependencySource,
    DependencyGraph
)
from services.query_translator import QueryTranslator

logger = logging.getLogger(__name__)
settings = get_settings()

CACHE_DIR = Path.cwd() / ".cache"
EMBEDDINGS_CACHE_FILE = CACHE_DIR / "tool_embeddings.json"
METADATA_CACHE_FILE = CACHE_DIR / "tool_metadata.json"
MANIFEST_CACHE_FILE = CACHE_DIR / "swagger_manifest.json"


class ToolRegistry:
    """
    Dynamic tool registry with 2-step discovery.

    Architecture:
    1. Load Swagger specs -> Parse -> Build UnifiedToolDefinition
    2. Generate embeddings for semantic search
    3. Build dependency graph for auto-chaining
    4. Persist to .cache/ for fast startup
    """

    # Blacklist patterns (generic)
    BLACKLIST_PATTERNS: Set[str] = {
        "batch", "excel", "export", "import", "internal",
        "count", "odata", "searchinfo", "swagger", "health"
    }

    # FIX #12: Offline Synonym Map for Expansion Search
    # Auto-generated from tool descriptions during initialization
    # Maps user queries to semantic variations for better discovery
    SYNONYM_MAP: Dict[str, List[str]] = {}

    # Auto-injectable params (context params) - MINSKA ZONA #1
    # All possible naming variations for system identifiers
    CONTEXT_PARAM_MAP: Dict[str, str] = {
        # Person/User ID variations
        "personid": "person_id",
        "person_id": "person_id",
        "assignedtoid": "person_id",
        "assigned_to_id": "person_id",
        "driverid": "person_id",
        "driver_id": "person_id",
        "userid": "person_id",
        "user_id": "person_id",
        "createdby": "person_id",
        "created_by": "person_id",
        "modifiedby": "person_id",
        "modified_by": "person_id",

        # Tenant ID variations
        "tenantid": "tenant_id",
        "tenant_id": "tenant_id",
        "x-tenant-id": "tenant_id",
        "x_tenant_id": "tenant_id",
        "organizationid": "tenant_id",
        "organization_id": "tenant_id",
    }

    MAX_TOOLS_PER_RESPONSE = 12  # Hard limit for LLM context

    # ═══════════════════════════════════════════════════════════════
    # METHOD DISAMBIGUATION CONFIGURATION (NEW v2.1)
    # ═══════════════════════════════════════════════════════════════
    # These values control how the system penalizes/boosts tools based on
    # detected user intent. All values are configurable to allow tuning
    # without code changes.
    #
    # DESIGN PRINCIPLE: The system detects user intent (read vs mutate)
    # and adjusts tool scores accordingly. This prevents dangerous
    # operations (DELETE) from being selected when user just wants to read.

    # Penalty factors for different scenarios
    DISAMBIGUATION_CONFIG = {
        # When user has CLEAR read intent (e.g., "koja je", "prikaži")
        "read_intent_penalty_factor": 0.25,

        # When intent is UNCLEAR (conservative approach)
        "unclear_intent_penalty_factor": 0.10,

        # Multiplier for DELETE operations (most dangerous)
        "delete_penalty_multiplier": 2.0,

        # Multiplier for PUT/PATCH operations
        "update_penalty_multiplier": 1.0,

        # Multiplier for POST create operations
        "create_penalty_multiplier": 0.5,

        # Boost for GET operations when read intent detected
        "get_boost_on_read_intent": 0.05,
    }



    def __init__(self, redis_client=None):
        """
        Initialize tool registry.

        Args:
            redis_client: Optional Redis for additional caching
        """
        self.redis = redis_client

        # OpenAI client for embeddings
        self.openai = AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION
        )

        # Storage
        self.tools: Dict[str, UnifiedToolDefinition] = {}
        self.embeddings: Dict[str, List[float]] = {}
        self.dependency_graph: Dict[str, DependencyGraph] = {}

        # Categorization
        self.retrieval_tools: Set[str] = set()
        self.mutation_tools: Set[str] = set()

        # State
        self.is_ready = False
        self._load_lock = asyncio.Lock()

        # Query translation for Croatian → English API terms
        self.translator = QueryTranslator()

        # Ensure cache directory exists
        CACHE_DIR.mkdir(exist_ok=True)

        logger.info("ToolRegistry initialized (v2.0)")

    async def load_swagger(self, source: str) -> bool:
        """
        Backward compatibility method for single Swagger source.

        DEPRECATED: Use initialize() with list of sources instead.
        This exists only for legacy code compatibility.
        """
        logger.warning(
            "⚠️ load_swagger() is DEPRECATED. "
            "Use initialize([sources]) instead."
        )

        # Delegate to initialize() with single source
        success = await self.initialize([source])
        return success

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
                # Check if cache is valid
                if await self._is_cache_valid(swagger_sources):
                    logger.info("📦 Loading from cache...")
                    await self._load_cache()
                    self.is_ready = True
                    logger.info(
                        f"✅ Loaded {len(self.tools)} tools from cache "
                        f"({len(self.retrieval_tools)} retrieval, "
                        f"{len(self.mutation_tools)} mutation)"
                    )
                    return True

                logger.info("🔄 Cache invalid - fetching Swagger specs...")

                # Ensure cache directory exists (CRITICAL for save)
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                logger.info(f"📁 Cache directory: {CACHE_DIR.absolute()}")

                # Fetch and parse specs
                for source in swagger_sources:
                    await self._load_swagger_spec(source)

                if not self.tools:
                    logger.error("❌ No tools loaded from Swagger sources")
                    return False

                logger.info(f"📦 Loaded {len(self.tools)} tools from Swagger")

                # Generate embeddings
                await self._generate_embeddings()

                # Build dependency graph
                self._build_dependency_graph()

                # Save cache (with verification)
                await self._save_cache(swagger_sources)

                self.is_ready = True

                logger.info(
                    f"✅ Initialized {len(self.tools)} tools "
                    f"({len(self.retrieval_tools)} retrieval, "
                    f"{len(self.mutation_tools)} mutation)"
                )

                return True

            except Exception as e:
                logger.error(f"❌ Initialization failed: {e}", exc_info=True)
                return False

    async def _is_cache_valid(self, swagger_sources: List[str]) -> bool:
        """
        Robust cache validation (MINSKA ZONA #4).

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

            if "embeddings" not in embeddings_data or not isinstance(embeddings_data["embeddings"], dict):
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

    async def _load_swagger_spec(self, url: str) -> None:
        """Load and parse single Swagger spec."""
        try:
            logger.info(f"Fetching: {url}")

            spec = await self._fetch_json(url)
            if not spec:
                logger.warning(f"Empty spec: {url}")
                return

            service_name = self._extract_service_name(url)
            service_url = self._extract_base_url(spec)

            paths = spec.get("paths", {})
            operations_parsed = 0

            for path, methods in paths.items():
                for method, operation in methods.items():
                    if method.lower() not in ["get", "post", "put", "patch", "delete"]:
                        continue

                    try:
                        tool = self._parse_operation(
                            service_name=service_name,
                            service_url=service_url,
                            path=path,
                            method=method.upper(),
                            operation=operation,
                            spec=spec
                        )

                        if tool:
                            self.tools[tool.operation_id] = tool

                            # Categorize
                            if tool.is_retrieval:
                                self.retrieval_tools.add(tool.operation_id)
                            if tool.is_mutation:
                                self.mutation_tools.add(tool.operation_id)

                            operations_parsed += 1

                    except Exception as e:
                        logger.debug(
                            f"Skipped {method} {path}: {e}"
                        )

            logger.info(
                f"✅ {service_name}: {operations_parsed} operations"
            )

        except Exception as e:
            logger.error(f"Failed to load {url}: {e}")

    def _parse_operation(
        self,
        service_name: str,
        service_url: str,
        path: str,
        method: str,
        operation: Dict,
        spec: Dict
    ) -> Optional[UnifiedToolDefinition]:
        """
        Parse OpenAPI operation into UnifiedToolDefinition.

        Returns None if operation should be skipped.
        """
        # Generate operation ID
        operation_id = operation.get("operationId")
        if not operation_id:
            operation_id = self._generate_operation_id(path, method)

        # Blacklist check
        if self._is_blacklisted(operation_id, path):
            return None

        # Extract description
        summary = operation.get("summary", "")
        description = operation.get("description", "")
        full_desc = f"{summary}. {description}".strip(". ")

        # Parse parameters
        parameters = {}
        required_params = []
        output_keys = []

        # URL/Query parameters
        for param in operation.get("parameters", []):
            param_def = self._parse_parameter(param)
            if param_def:
                parameters[param_def.name] = param_def
                if param_def.required:
                    required_params.append(param_def.name)

        # Request body
        if "requestBody" in operation:
            body_params = self._parse_request_body(
                operation["requestBody"],
                spec
            )
            parameters.update(body_params)

            for param_name, param_def in body_params.items():
                if param_def.required:
                    required_params.append(param_name)

        # Infer output keys from responses
        output_keys = self._infer_output_keys(operation, spec)

        # Build embedding text (CRITICAL: Include output_keys for Deep Content Indexing)
        embedding_text = self._build_embedding_text(
            operation_id,
            service_name,
            path,
            method,
            full_desc,
            parameters,
            output_keys  # NEW: Enable discovery by output fields
        )

        # Extract swagger_name from service_url
        # service_url can be:
        # - "/automation" → swagger_name = "automation"
        # - "https://api.example.com/automation" → swagger_name = "automation"
        # - "" (empty) → swagger_name = ""
        swagger_name = ""
        if service_url:
            # Remove leading slash if present
            clean_url = service_url.lstrip("/")
            # If it's a relative path like "automation", use it directly
            if not clean_url.startswith("http"):
                swagger_name = clean_url
            else:
                # If it's an absolute URL, extract last path segment
                # e.g., "https://api.example.com/automation" → "automation"
                from urllib.parse import urlparse
                parsed = urlparse(clean_url)
                path_parts = [p for p in parsed.path.split("/") if p]
                if path_parts:
                    swagger_name = path_parts[0]

        # Create tool definition
        tool = UnifiedToolDefinition(
            operation_id=operation_id,
            service_name=service_name,
            swagger_name=swagger_name,
            service_url=service_url,
            path=path,
            method=method,
            description=full_desc,
            summary=summary,
            parameters=parameters,
            required_params=required_params,
            output_keys=output_keys,
            tags=operation.get("tags", []),
            embedding_text=embedding_text
        )

        return tool

    def _parse_parameter(self, param: Dict) -> Optional[ParameterDefinition]:
        """Parse OpenAPI parameter into ParameterDefinition."""
        param_name = param.get("name")
        if not param_name:
            return None

        location = param.get("in", "query")

        # Skip headers (handled separately)
        if location == "header":
            return None

        schema = param.get("schema", {})

        # Determine dependency source
        param_lower = param_name.lower()
        if param_lower in self.CONTEXT_PARAM_MAP:
            dependency_source = DependencySource.FROM_CONTEXT
            context_key = self.CONTEXT_PARAM_MAP[param_lower]
        else:
            dependency_source = DependencySource.FROM_USER
            context_key = None

        return ParameterDefinition(
            name=param_name,
            param_type=schema.get("type", "string"),
            format=schema.get("format"),
            description=param.get("description", "")[:200],
            required=param.get("required", False),
            location=location,
            dependency_source=dependency_source,
            context_key=context_key,
            enum_values=schema.get("enum")
        )

    def _parse_request_body(
        self,
        request_body: Dict,
        spec: Dict
    ) -> Dict[str, ParameterDefinition]:
        """Parse request body into parameter definitions."""
        params = {}

        content = request_body.get("content", {})
        json_content = content.get("application/json", {})
        schema = json_content.get("schema", {})

        schema = self._resolve_ref(schema, spec)
        required_fields = set(schema.get("required", []))

        for prop_name, prop_schema in schema.get("properties", {}).items():
            prop_schema = self._resolve_ref(prop_schema, spec)

            # Determine dependency source
            prop_lower = prop_name.lower()
            if prop_lower in self.CONTEXT_PARAM_MAP:
                dependency_source = DependencySource.FROM_CONTEXT
                context_key = self.CONTEXT_PARAM_MAP[prop_lower]
            else:
                dependency_source = DependencySource.FROM_USER
                context_key = None

            params[prop_name] = ParameterDefinition(
                name=prop_name,
                param_type=prop_schema.get("type", "string"),
                format=prop_schema.get("format"),
                description=prop_schema.get("description", "")[:200],
                required=prop_name in required_fields,
                location="body",
                dependency_source=dependency_source,
                context_key=context_key,
                enum_values=prop_schema.get("enum"),
                items_type=prop_schema.get("items", {}).get("type")
            )

        return params

    def _infer_output_keys(
        self,
        operation: Dict,
        spec: Dict
    ) -> List[str]:
        """
        Infer output keys from response schema (MINSKA ZONA #3).

        Enhanced to recognize:
        - UUID variations (Id, ID, id, uuid, Uuid, UUID)
        - VIN, plate, code synonyms
        - Nested data structures (Items[], Data{})

        This helps with dependency chaining and prevents broken tool sequences.
        """
        output_keys = []

        responses = operation.get("responses", {})
        success_response = (
            responses.get("200") or
            responses.get("201") or
            responses.get("204")
        )

        if not success_response:
            return output_keys

        content = success_response.get("content", {})
        json_content = content.get("application/json", {})
        schema = json_content.get("schema", {})

        schema = self._resolve_ref(schema, spec)

        # CRITICAL FIX: Handle direct array responses (e.g., MasterData returns array)
        # If response is array type, extract items schema first
        if schema.get("type") == "array":
            items_schema = schema.get("items", {})
            schema = self._resolve_ref(items_schema, spec)

        # Check for array responses (common pattern: {Items: [...], Count: 10})
        properties = schema.get("properties", {})

        # If response has "Items" or "Data" array, look inside
        for wrapper_key in ["Items", "items", "Data", "data", "results", "value"]:
            if wrapper_key in properties:
                wrapper_schema = self._resolve_ref(properties[wrapper_key], spec)

                # If it's an array, get items schema
                if wrapper_schema.get("type") == "array":
                    items_schema = wrapper_schema.get("items", {})
                    items_schema = self._resolve_ref(items_schema, spec)
                    properties = items_schema.get("properties", {})
                    break

        # Extract all property names
        all_keys = list(properties.keys())

        # čemu ovo služi ? 
        # Expanded useful patterns for chaining (MINSKA ZONA #3 fix)
        useful_patterns = [
            # ID variations
            "id", "ID", "Id", "uuid", "UUID", "Uuid",
            # Business identifiers
            "vin", "VIN", "Vin",
            "plate", "Plate", "LicencePlate", "RegistrationNumber",
            "code", "Code",
            "number", "Number",
            "name", "Name",
            # Specific domain IDs (generic, not hardcoded domain)
            "externalId", "ExternalId", "external_id",
            "referenceId", "ReferenceId", "reference_id",
        ]

        # Filter keys that match useful patterns
        for key in all_keys:
            if any(pattern in key for pattern in useful_patterns):
                output_keys.append(key)

        # Also add keys ending with "Id" or "UUID"
        for key in all_keys:
            if key.endswith("Id") or key.endswith("ID") or key.endswith("UUID"):
                if key not in output_keys:
                    output_keys.append(key)

        return output_keys[:15]  # Increased limit to 15 keys

    def _resolve_ref(self, schema: Dict, spec: Dict) -> Dict:
        """Resolve $ref to actual schema."""
        if not isinstance(schema, dict):
            return schema

        if "$ref" not in schema:
            return schema

        ref_path = schema["$ref"]
        if not ref_path.startswith("#/"):
            return schema

        parts = ref_path[2:].split("/")
        resolved = spec

        for part in parts:
            resolved = resolved.get(part, {})

        return resolved

    def _build_embedding_text(
        self,
        operation_id: str,
        service_name: str,
        path: str,
        method: str,
        description: str,
        parameters: Dict[str, ParameterDefinition],
        output_keys: List[str] = None
    ) -> str:
        """
        Build RICH text for semantic embedding (Deep Content Indexing).

        CRITICAL: Includes output_keys to enable discovery by return values.

        Example:
            Query: "prikaži registraciju" → finds tool with "RegistrationNumber" in output_keys
            Query: "kolika je kilometraža" → finds tool with "Mileage" in output_keys

        This is the KEY to content-driven discovery vs name-only matching.
        """
        parts = [
            f"Operation: {operation_id}",
            f"Service: {service_name}",
            f"Method: {method} {path}",
            f"Description: {description}"
        ]

        # Add input parameters (what user provides)
        if parameters:
            param_names = [
                p.name for p in parameters.values()
                if p.dependency_source != DependencySource.FROM_CONTEXT
            ]
            if param_names:
                parts.append(f"Parameters: {', '.join(param_names)}")

        # CRITICAL FIX: Add output keys (what tool returns)
        # This enables "registracija" → "RegistrationNumber" discovery
        if output_keys:
            # Convert camelCase/PascalCase to human-readable
            # "RegistrationNumber" → "Registration Number"
            # "LicencePlate" → "Licence Plate"
            human_readable_outputs = []
            for key in output_keys:
                # Insert space before capital letters
                spaced = re.sub(r'([a-z])([A-Z])', r'\1 \2', key)
                human_readable_outputs.append(spaced)

            parts.append(f"Returns: {', '.join(human_readable_outputs)}")

            # Also add raw keys for exact matching
            parts.append(f"Output fields: {', '.join(output_keys)}")

        text = ". ".join(parts)

        # Truncate to 1500 chars at sentence boundary
        if len(text) > 1500:
            text = text[:1500]
            last_period = text.rfind(". ")
            if last_period > 1300:
                text = text[:last_period + 1]

        return text

    def _generate_operation_id(self, path: str, method: str) -> str:
        """Generate operation ID from path and method."""
        clean = re.sub(r"[^a-zA-Z0-9]", "_", path)
        clean = re.sub(r"_+", "_", clean).strip("_")
        return f"{method.lower()}_{clean}"

    def _is_blacklisted(self, operation_id: str, path: str) -> bool:
        """Check if operation should be blacklisted."""
        combined = f"{operation_id.lower()} {path.lower()}"
        return any(pattern in combined for pattern in self.BLACKLIST_PATTERNS)

    def _extract_service_name(self, url: str) -> str:
        """Extract service name from Swagger URL."""
        # Example: .../automation/swagger/v1.0.0/swagger.json -> automation
        parts = url.split("/")
        for i, part in enumerate(parts):
            if part == "swagger" and i > 0:
                return parts[i - 1]
        return "unknown"

    def _extract_base_url(self, spec: Dict) -> str:
        """
        Extract base URL from OpenAPI spec.

        CRITICAL FIX (MINSKA ZONA #2): Handle multiple servers, relative URLs,
        and validate that URL is absolute.

        Priority:
        1. servers[0].url (OpenAPI 3.x)
        2. basePath (OpenAPI 2.x)
        3. Fallback to empty string
        """
        # OpenAPI 3.x: servers array
        if "servers" in spec and spec["servers"]:
            servers = spec["servers"]

            # Prefer production server if multiple exist
            for server in servers:
                url = server.get("url", "")
                description = server.get("description", "").lower()

                # Skip development/test servers
                if any(kw in description for kw in ["dev", "test", "local", "staging"]):
                    continue

                # Validate absolute URL
                if url.startswith("http://") or url.startswith("https://"):
                    return url.rstrip("/")

            # Fallback: first server (even if dev)
            first_url = servers[0].get("url", "")
            if first_url.startswith("http"):
                return first_url.rstrip("/")
            # CRITICAL FIX: Return relative URL (like "/automation")
            if first_url:
                return first_url.rstrip("/")

        # OpenAPI 2.x: basePath
        if "basePath" in spec:
            base_path = spec["basePath"]
            # If relative, try to construct absolute URL from host + schemes
            if not base_path.startswith("http"):
                host = spec.get("host", "")
                schemes = spec.get("schemes", ["https"])
                if host:
                    return f"{schemes[0]}://{host}{base_path}".rstrip("/")
            return base_path.rstrip("/")

        return ""

    async def _generate_embeddings(self) -> None:
        """Generate embeddings for all tools."""
        missing = [
            op_id for op_id in self.tools
            if op_id not in self.embeddings
        ]

        if not missing:
            logger.info("All embeddings cached")
            return

        logger.info(f"Generating {len(missing)} embeddings...")

        for op_id in missing:
            tool = self.tools[op_id]
            text = tool.embedding_text

            embedding = await self._get_embedding(text)
            if embedding:
                self.embeddings[op_id] = embedding

            await asyncio.sleep(0.05)  # Rate limiting

        logger.info(f"✅ Generated {len(missing)} embeddings")

    async def _get_embedding(self, text: str) -> Optional[List[float]]:
        """Get embedding for text."""
        try:
            response = await self.openai.embeddings.create(
                input=[text[:8000]],
                model=settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
            )
            return response.data[0].embedding
        except Exception as e:
            logger.warning(f"Embedding error: {e}")
            return None

    def _build_dependency_graph(self) -> None:
        """Build dependency graph for automatic chaining."""
        logger.info("Building dependency graph...")

        for tool_id, tool in self.tools.items():
            # Find parameters that need FROM_TOOL_OUTPUT
            output_params = tool.get_output_params()
            required_outputs = list(output_params.keys())

            # Find tools that provide these outputs
            provider_tools = []
            for req_output in required_outputs:
                providers = self._find_providers(req_output)
                provider_tools.extend(providers)

            if required_outputs:
                self.dependency_graph[tool_id] = DependencyGraph(
                    tool_id=tool_id,
                    required_outputs=required_outputs,
                    provider_tools=list(set(provider_tools))
                )

        logger.info(
            f"Built dependency graph: "
            f"{len(self.dependency_graph)} tools with dependencies"
        )

    def _find_providers(self, output_key: str) -> List[str]:
        """Find tools that provide given output key."""
        providers = []

        for tool_id, tool in self.tools.items():
            if output_key in tool.output_keys:
                providers.append(tool_id)
            # Case-insensitive match
            elif any(
                ok.lower() == output_key.lower()
                for ok in tool.output_keys
            ):
                providers.append(tool_id)

        return providers

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
        FAZA 1: Semantic search for relevant tools.

        Args:
            query: User query
            top_k: Number of base tools (before boosting)
            prefer_retrieval: Search only retrieval tools
            prefer_mutation: Search only mutation tools
            threshold: Similarity threshold

        Returns:
            List of tools with dependency-boosted results (max 12 total)
        """
        if not self.is_ready:
            logger.warning("Registry not ready")
            return []

        # FIX: Translate Croatian → English for better semantic matching
        enhanced_query = self.translator.translate_query(query)
        if enhanced_query != query:
            logger.info(f"🌐 Query enhanced: '{query}' → '{enhanced_query}'")

        threshold = threshold or settings.SIMILARITY_THRESHOLD
        query_embedding = await self._get_embedding(enhanced_query)

        if not query_embedding:
            return await self._fallback_search(query, top_k)

        # Determine search pool
        search_pool = set(self.tools.keys())
        if prefer_retrieval:
            search_pool = self.retrieval_tools
            logger.info(
                f"🔍 Searching RETRIEVAL tools ({len(search_pool)})"
            )
        elif prefer_mutation:
            search_pool = self.mutation_tools
            logger.info(
                f"⚡ Searching MUTATION tools ({len(search_pool)})"
            )

        # FIX #12: Adaptive threshold for expansion search
        # PRIMARY PASS: Strict threshold
        strict_threshold = threshold
        lenient_threshold = max(0.40, threshold - 0.20)  # VERY RELAXED for better discovery

        scored = []
        for op_id in search_pool:
            if op_id not in self.embeddings:
                continue

            similarity = self._cosine_similarity(
                query_embedding,
                self.embeddings[op_id]
            )

            # Accept all above lenient threshold for candidate pool
            if similarity >= lenient_threshold:
                scored.append((similarity, op_id))

        # FIX: Domain-specific boosting (Croatian context awareness)
        domain_boosts = self.translator.get_domain_hints(query)
        if domain_boosts:
            boosted_scored = []
            for similarity, op_id in scored:
                boost = 0.0
                op_id_lower = op_id.lower()
                for pattern, boost_score in domain_boosts.items():
                    if pattern in op_id_lower:
                        boost = max(boost, boost_score)
                boosted_scored.append((similarity + boost, op_id))
            scored = boosted_scored
            logger.info(f"🎯 Applied domain boosts: {domain_boosts}")

        # NEW v2.1: GET/DELETE DISAMBIGUATION
        scored = self._apply_method_disambiguation(query, scored)

        scored.sort(key=lambda x: x[0], reverse=True)

        # FIX #12: Description-based re-ranking for low-scoring matches
        if len(scored) < top_k and len(scored) > 0:
            # Expand with description keyword matching
            description_matches = self._description_keyword_search(query, search_pool)
            for op_id, desc_score in description_matches:
                if op_id not in [s[1] for s in scored]:
                    # Add with boosted score (hybrid: embedding + keyword)
                    scored.append((desc_score * 0.7, op_id))  # Weight description lower

            scored.sort(key=lambda x: x[0], reverse=True)
            logger.info(f"🔍 Expansion search added {len(description_matches)} keyword matches")

        # Log top matches
        if scored:
            top_matches = [
                (f"{score:.3f}", op_id)
                for score, op_id in scored[:10]  # Show more for debugging
            ]
            logger.info(f"🎯 Top matches (expanded): {top_matches}")
        else:
            logger.warning("⚠️ No matches found - trying fallback")
            return await self._fallback_search(query, top_k)

        # FAZA 2: Dependency Boosting
        base_tools = [op_id for _, op_id in scored[:top_k]]
        boosted_tools = await self._apply_dependency_boosting(base_tools)

        # Limit to MAX_TOOLS_PER_RESPONSE
        final_tools = boosted_tools[:self.MAX_TOOLS_PER_RESPONSE]

        logger.info(
            f"📦 Returning {len(final_tools)} tools "
            f"({top_k} base + {len(final_tools) - top_k} boosted)"
        )

        # Convert to OpenAI format
        return [
            self.tools[tool_id].to_openai_function()
            for tool_id in final_tools
        ]

    async def find_relevant_tools_with_scores(
        self,
        query: str,
        top_k: int = 5,
        threshold: float = 0.55,
        prefer_retrieval: bool = False,
        prefer_mutation: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Find relevant tools WITH SIMILARITY SCORES.

        Same as find_relevant_tools but returns scores for ReasoningEngine.

        NEW v2.1: GET/DELETE DISAMBIGUATION
        When query is a read-type question (what is X, show me, etc.),
        penalize DELETE/PUT/PATCH methods to prevent accidental mutations.

        Returns:
            List of dicts with:
            {
                "name": str,
                "score": float,
                "schema": dict  # OpenAI function schema
            }
        """
        # Translate Croatian → English
        enhanced_query = self.translator.translate_query(query)
        logger.debug(f"Enhanced query: {query} → {enhanced_query}")

        query_embedding = await self._get_embedding(enhanced_query)

        if not query_embedding:
            fallback = await self._fallback_search(query, top_k)
            # Convert to with_scores format
            return [
                {"name": tool["function"]["name"], "score": 0.0, "schema": tool}
                for tool in fallback
            ]

        # Determine search pool
        search_pool = set(self.tools.keys())
        if prefer_retrieval:
            search_pool = self.retrieval_tools
        elif prefer_mutation:
            search_pool = self.mutation_tools

        # Adaptive threshold
        lenient_threshold = max(0.40, threshold - 0.20)

        scored = []
        for op_id in search_pool:
            if op_id not in self.embeddings:
                continue

            similarity = self._cosine_similarity(
                query_embedding,
                self.embeddings[op_id]
            )

            if similarity >= lenient_threshold:
                scored.append((similarity, op_id))

        # Domain boosting
        domain_boosts = self.translator.get_domain_hints(query)
        if domain_boosts:
            boosted_scored = []
            for similarity, op_id in scored:
                boost = 0.0
                op_id_lower = op_id.lower()
                for pattern, boost_score in domain_boosts.items():
                    if pattern in op_id_lower:
                        boost = max(boost, boost_score)
                boosted_scored.append((similarity + boost, op_id))
            scored = boosted_scored

        # NEW v2.1: GET/DELETE DISAMBIGUATION
        # Detect if query is read-type (what is X, show me, prikaži, koja je)
        # and penalize dangerous mutations
        scored = self._apply_method_disambiguation(query, scored)

        # NEW v13.0: USER-SPECIFIC INTENT BOOSTING
        # When user says "moje vozilo", "moj auto", "meni dodijeljeno"
        # boost tools that have personId parameter (like get_MasterData)
        scored = self._apply_user_specific_boosting(query, scored)

        # NEW v13.0: PERFORMANCE-BASED EVALUATION
        # Apply boost/penalty based on tool success rate and user feedback
        scored = self._apply_evaluation_adjustment(scored)

        scored.sort(key=lambda x: x[0], reverse=True)

        # Expansion search
        if len(scored) < top_k and len(scored) > 0:
            description_matches = self._description_keyword_search(query, search_pool)
            for op_id, desc_score in description_matches:
                if op_id not in [s[1] for s in scored]:
                    scored.append((desc_score * 0.7, op_id))
            scored.sort(key=lambda x: x[0], reverse=True)

        if not scored:
            fallback = await self._fallback_search(query, top_k)
            return [
                {"name": tool["function"]["name"], "score": 0.0, "schema": tool}
                for tool in fallback
            ]

        # Dependency boosting
        base_tools = [op_id for _, op_id in scored[:top_k]]
        boosted_tools = await self._apply_dependency_boosting(base_tools)

        # Limit to MAX_TOOLS_PER_RESPONSE
        final_tools = boosted_tools[:self.MAX_TOOLS_PER_RESPONSE]

        # Build result with scores
        result = []
        scored_dict = {op_id: score for score, op_id in scored}

        for tool_id in final_tools:
            schema = self.tools[tool_id].to_openai_function()
            score = scored_dict.get(tool_id, 0.0)
            result.append({
                "name": tool_id,
                "score": score,
                "schema": schema
            })

        logger.info(f"📦 Returning {len(result)} tools with scores")

        return result

    async def _apply_dependency_boosting(
        self,
        base_tools: List[str]
    ) -> List[str]:
        """
        FAZA 2: Dependency Boosting Logic Loop.

        For each base tool with dependencies, add provider tools.
        """
        result = list(base_tools)

        for tool_id in base_tools:
            dep_graph = self.dependency_graph.get(tool_id)
            if not dep_graph:
                continue

            # Add provider tools
            for provider_id in dep_graph.provider_tools[:2]:  # Max 2 per tool
                if provider_id not in result:
                    result.append(provider_id)
                    logger.debug(
                        f"🔗 Boosted {provider_id} for {tool_id}"
                    )

        return result

    async def _fallback_search(
        self,
        query: str,
        top_k: int
    ) -> List[Dict[str, Any]]:
        """Fallback keyword search if embedding fails."""
        query_lower = query.lower()
        matches = []

        for op_id, tool in self.tools.items():
            text = f"{tool.description} {tool.path}".lower()
            score = sum(
                1 for word in query_lower.split()
                if word in text
            )

            if score > 0:
                matches.append((score, op_id))

        matches.sort(key=lambda x: x[0], reverse=True)

        tool_ids = [op_id for _, op_id in matches[:top_k]]
        return [
            self.tools[tool_id].to_openai_function()
            for tool_id in tool_ids
        ]

    def _description_keyword_search(
        self,
        query: str,
        search_pool: Set[str],
        max_results: int = 5
    ) -> List[Tuple[str, float]]:
        """
        FIX #12: Description-based keyword matching for expansion.

        Searches tool descriptions for keyword matches to supplement
        semantic search. Useful when embeddings miss synonym relationships.

        Args:
            query: User query
            search_pool: Set of tool IDs to search within
            max_results: Maximum number of results

        Returns:
            List of (tool_id, score) tuples
        """
        query_lower = query.lower()
        query_words = set(query_lower.split())

        matches = []

        for op_id in search_pool:
            tool = self.tools.get(op_id)
            if not tool:
                continue

            # Build searchable text from multiple fields INCLUDING PARAMETERS AND OUTPUT KEYS
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
                param_text,  # CRITICAL: Search in parameter names/descriptions
                output_text   # CRITICAL: Search in output keys (like "RegistrationNumber")
            ])

            # Calculate match score (word overlap + partial matching)
            score = 0.0

            # Exact word matches
            searchable_words = set(searchable_text.split())
            word_overlap = len(query_words & searchable_words)
            score += word_overlap * 0.5

            # Partial substring matches (e.g., "registr" matches "registracija")
            for q_word in query_words:
                if len(q_word) >= 4:  # Min 4 chars for substring
                    if q_word in searchable_text:
                        score += 0.3

            if score > 0:
                matches.append((op_id, score))

        # Sort by score descending
        matches.sort(key=lambda x: x[1], reverse=True)

        return matches[:max_results]

    def _cosine_similarity(
        self,
        a: List[float],
        b: List[float]
    ) -> float:
        """Calculate cosine similarity."""
        if not a or not b or len(a) != len(b):
            return 0.0

        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot / (norm_a * norm_b)



    # ova funkcija je poprilično hardkodirana 
    def _apply_method_disambiguation(
        self,
        query: str,
        scored: List[Tuple[float, str]]
    ) -> List[Tuple[float, str]]:
        """
        NEW v2.1: GET/DELETE DISAMBIGUATION

        Problem iz logova:
            Korisnik: "Kako se zove moje vozilo"
            Sustav bira: delete_Vehicles (score 0.955) ❌

        Rješenje:
            Detektiraj "read intent" u query-ju i penaliziraj DELETE/PUT/PATCH.

        Read intent patterns (Croatian + English):
            - "koja je", "što je", "kolika je", "kakva je"
            - "prikaži", "pokaži", "daj mi", "reci mi"
            - "what is", "show me", "tell me", "get"
            - pitanja sa "?"

        Args:
            query: User query
            scored: List of (score, operation_id) tuples

        Returns:
            Modified scored list with penalties applied
        """
        query_lower = query.lower().strip()

        # Patterns indicating READ intent
        read_patterns = [
            # Croatian questions
            r'koja?\s+je', r'što\s+je', r'kolika?\s+je', r'kakv[aoi]?\s+je',
            r'koja?\s+su', r'koji?\s+su', r'koliko\s+ima',
            r'kako\s+se\s+zove', r'sto\s+je', r'kakvo\s+je',  # Additional Croatian
            # Croatian commands for reading
            r'prikaži', r'pokaži', r'daj\s+mi', r'reci\s+mi',
            r'pronađi', r'nađi', r'traži', r'pretraži',
            r'prikazi', r'pokazi',  # Without diacritics
            # English questions
            r'what\s+is', r'what\s+are', r'how\s+much', r'how\s+many',
            r'show\s+me', r'tell\s+me', r'get\s+me', r'find',
            r'list', r'display', r'view',
            # Common question patterns
            r'\?$',  # Ends with question mark
            r'^ima\s+li', r'^postoji\s+li',  # Croatian questions
            r'^is\s+there', r'^are\s+there',  # English questions
        ]

        # Patterns indicating MUTATION intent (user explicitly wants to change)
        mutation_patterns = [
            r'obriši', r'izbriši', r'ukloni', r'makni',  # Croatian delete
            r'delete', r'remove', r'erase', r'destroy',  # English delete
            r'dodaj', r'kreiraj', r'napravi', r'stvori',  # Croatian create
            r'add', r'create', r'make', r'new',  # English create
            r'promijeni', r'ažuriraj', r'izmijeni', r'uredi',  # Croatian update
            r'update', r'change', r'modify', r'edit',  # English update
            r'želim\s+obrisati', r'hoću\s+obrisati',  # Explicit Croatian intent
        ]

        # Check if query has read intent
        is_read_intent = any(
            re.search(pattern, query_lower)
            for pattern in read_patterns
        )

        # Check if query has explicit mutation intent
        is_mutation_intent = any(
            re.search(pattern, query_lower)
            for pattern in mutation_patterns
        )

        # If mutation intent is explicit, don't penalize
        if is_mutation_intent:
            logger.debug("Detected MUTATION intent - no penalties applied")
            return scored

        # Get config values (allows runtime tuning without code changes)
        config = self.DISAMBIGUATION_CONFIG

        # If no clear read intent either, be conservative
        if not is_read_intent:
            logger.debug("No clear intent detected - applying light penalties")
            penalty_factor = config["unclear_intent_penalty_factor"]
        else:
            logger.info(f"🔍 Detected READ intent in: '{query}'")
            penalty_factor = config["read_intent_penalty_factor"]

        # Apply penalties to dangerous methods
        adjusted = []
        for score, op_id in scored:
            tool = self.tools.get(op_id)
            if not tool:
                adjusted.append((score, op_id))
                continue

            op_id_lower = op_id.lower()

            # Penalize DELETE methods heavily
            if tool.method == "DELETE" or "delete" in op_id_lower:
                penalty = penalty_factor * config["delete_penalty_multiplier"]
                new_score = max(0, score - penalty)
                if score != new_score:
                    logger.debug(
                        f"🔻 Penalized DELETE: {op_id} "
                        f"({score:.3f} → {new_score:.3f})"
                    )
                adjusted.append((new_score, op_id))

            # Penalize PUT/PATCH (updates)
            elif tool.method in ("PUT", "PATCH") or any(
                x in op_id_lower for x in ["update", "put", "patch"]
            ):
                penalty = penalty_factor * config["update_penalty_multiplier"]
                new_score = max(0, score - penalty)
                adjusted.append((new_score, op_id))

            # Penalize POST (creates/mutations) when read intent detected
            elif tool.method == "POST" and "get" not in op_id_lower:
                # v13.1 FIX: ALL POST methods get penalized for read intent
                # Previous bug: post_Vehicles wasn't penalized because it lacked "create" keyword
                # POST is a mutation method - if user wants data, they need GET

                # Check if it's a search/query POST (rare but exists)
                is_search_post = any(x in op_id_lower for x in ["search", "query", "find", "filter"])

                if is_search_post:
                    # Search POSTs are pseudo-GETs, don't penalize
                    adjusted.append((score, op_id))
                else:
                    # All other POSTs get penalized for read intent
                    penalty = penalty_factor * config["create_penalty_multiplier"]
                    new_score = max(0, score - penalty)
                    logger.debug(
                        f"🔻 Penalized POST: {op_id} "
                        f"({score:.3f} → {new_score:.3f})"
                    )
                    adjusted.append((new_score, op_id))

            # Boost GET methods when read intent detected
            elif tool.method == "GET" and is_read_intent:
                boost = config["get_boost_on_read_intent"]
                adjusted.append((score + boost, op_id))

            else:
                adjusted.append((score, op_id))

        return adjusted

    def _apply_user_specific_boosting(
        self,
        query: str,
        scored: List[Tuple[float, str]]
    ) -> List[Tuple[float, str]]:
        """
        NEW v13.0: USER-SPECIFIC INTENT BOOSTING

        Problem iz logova:
            Korisnik: "Koji je auto meni zadan" ili "moje vozilo"
            Sustav bira: get_Vehicles (vraća SVA vozila tenanta) ❌
            Trebao bi: get_MasterData (vraća podatke za OSOBU) ✓

        Rješenje:
            1. Detektiraj "user-specific intent" u query-ju
            2. Analiziraj parametre alata - ima li personId/AssignedToId?
            3. Boost alate koji podržavaju filtriranje po korisniku
            4. Penaliziraj alate koji vraćaju sve podatke (bez personId parametra)

        User-specific patterns (Croatian + English):
            - "moje", "moj", "moja", "moji" (my)
            - "meni", "mi" (to me, for me)
            - "dodijeljeno mi", "zadan mi" (assigned to me)
            - "za mene" (for me)
            - "prikaži mi moje" (show me my)

        Args:
            query: User query
            scored: List of (score, operation_id) tuples

        Returns:
            Modified scored list with user-specific boosting applied
        """
        query_lower = query.lower().strip()

        # Patterns indicating USER-SPECIFIC intent
        user_specific_patterns = [
            # Croatian possessive pronouns
            r'\bmoje?\b', r'\bmoja\b', r'\bmoji\b',  # my, mine
            r'\bmeni\b', r'\bmi\b',  # to me, for me
            # Croatian phrases
            r'dodijeljeno\s+mi', r'zadan[ao]?\s+mi',  # assigned to me
            r'za\s+mene', r'pripada\s+mi',  # for me, belongs to me
            r'imam\s+li', r'imali?\b',  # do I have
            r'moj[aei]?\s+vozil', r'moj[aei]?\s+auto',  # my vehicle, my car
            r'koji\s+auto.*meni', r'koje\s+vozilo.*meni',  # which car is mine
            # English equivalents
            r'\bmy\b', r'\bmine\b', r'\bme\b',
            r'assigned\s+to\s+me', r'for\s+me', r'belongs\s+to\s+me',
            r'do\s+i\s+have', r'i\s+have',
        ]

        # Check if query has user-specific intent
        is_user_specific = any(
            re.search(pattern, query_lower)
            for pattern in user_specific_patterns
        )

        if not is_user_specific:
            logger.debug("No user-specific intent detected")
            return scored

        logger.info(f"👤 Detected USER-SPECIFIC intent in: '{query}'")

        # Parameters that indicate tool supports user-specific filtering
        user_filter_params = {
            # Primary person identifiers
            'personid', 'person_id',
            'assignedtoid', 'assigned_to_id',
            'driverid', 'driver_id',
            'userid', 'user_id',
            # Secondary identifiers
            'ownerid', 'owner_id',
            'employeeid', 'employee_id',
            'createdby', 'created_by',
        }

        # Apply boosting/penalties based on tool parameters
        adjusted = []
        boost_value = 0.15  # Significant boost for user-filterable tools
        penalty_value = 0.10  # Penalty for tools without user filtering
        masterdata_boost = 0.25  # Extra boost for get_MasterData (v13.2 FIX)
        calendar_penalty = 0.20  # Penalty for Calendar endpoints on "moje vozilo" queries

        for score, op_id in scored:
            tool = self.tools.get(op_id)
            if not tool:
                adjusted.append((score, op_id))
                continue

            # Check if tool has any user-filter parameters
            tool_params_lower = {p.lower() for p in tool.parameters.keys()}
            has_user_filter = bool(tool_params_lower & user_filter_params)

            # Also check output_keys for MasterData-like responses
            # MasterData returns user-specific data like "Vehicle", "Person", etc.
            output_keys_lower = {k.lower() for k in tool.output_keys}
            is_master_data_like = any(
                key in op_id.lower()
                for key in ['masterdata', 'person', 'profile', 'user', 'employee']
            )

            # v13.2 FIX: Special handling for "moje vozilo" queries
            # get_EquipmentCalendar* endpoints match "vozilo" in embedding but are WRONG for this query
            # get_MasterData is the CORRECT tool - it returns user's assigned vehicles
            op_id_lower = op_id.lower()

            # PRIORITY 1: get_MasterData gets highest boost for "moje vozilo"
            if 'masterdata' in op_id_lower:
                new_score = score + masterdata_boost + boost_value
                logger.info(
                    f"⬆️ MASTERDATA BOOST: {op_id} "
                    f"({score:.3f} → {new_score:.3f})"
                )
                adjusted.append((new_score, op_id))

            # PRIORITY 2: Calendar endpoints get penalized for "moje vozilo" queries
            # These are for viewing SCHEDULES, not vehicle INFO
            elif 'calendar' in op_id_lower or 'equipment' in op_id_lower:
                new_score = max(0, score - calendar_penalty)
                logger.debug(
                    f"⬇️ Calendar penalty: {op_id} "
                    f"({score:.3f} → {new_score:.3f})"
                )
                adjusted.append((new_score, op_id))

            # PRIORITY 3: Other user-filterable tools get standard boost
            elif has_user_filter or is_master_data_like:
                new_score = score + boost_value
                logger.debug(
                    f"⬆️ Boosted (user-filterable): {op_id} "
                    f"({score:.3f} → {new_score:.3f})"
                )
                adjusted.append((new_score, op_id))

            # PRIORITY 4: GET vehicle tools without personId - PENALIZE
            elif tool.method == "GET" and 'vehicle' in op_id_lower:
                new_score = max(0, score - penalty_value)
                logger.debug(
                    f"⬇️ Penalized (no user filter): {op_id} "
                    f"({score:.3f} → {new_score:.3f})"
                )
                adjusted.append((new_score, op_id))

            else:
                # Other tools - no change
                adjusted.append((score, op_id))

        return adjusted

    def _apply_evaluation_adjustment(
        self,
        scored: List[Tuple[float, str]]
    ) -> List[Tuple[float, str]]:
        """
        NEW v13.0: PERFORMANCE-BASED EVALUATION

        Apply boost/penalty based on historical tool performance:
        - Tools with high success rate get a boost
        - Tools with many failures get a penalty
        - User feedback (positive/negative) affects score

        This enables the system to LEARN which tools work well.

        Args:
            scored: List of (score, operation_id) tuples

        Returns:
            Modified scored list with evaluation adjustments
        """
        try:
            from services.tool_evaluator import get_tool_evaluator
            evaluator = get_tool_evaluator()
        except ImportError:
            logger.debug("Tool evaluator not available - skipping evaluation adjustment")
            return scored

        adjusted = []
        for score, op_id in scored:
            new_score = evaluator.apply_evaluation_adjustment(op_id, score)
            adjusted.append((new_score, op_id))

        return adjusted

    # ═══════════════════════════════════════════════
    # CACHE MANAGEMENT
    # ═══════════════════════════════════════════════

    async def _load_cache(self) -> None:
        """Load tools and embeddings from cache."""
        try:
            # Load metadata
            metadata = await asyncio.to_thread(
                self._read_json_sync,
                METADATA_CACHE_FILE
            )

            # Reconstruct tools
            for tool_dict in metadata.get("tools", []):
                tool = UnifiedToolDefinition(**tool_dict)
                self.tools[tool.operation_id] = tool

                if tool.is_retrieval:
                    self.retrieval_tools.add(tool.operation_id)
                if tool.is_mutation:
                    self.mutation_tools.add(tool.operation_id)

            # Load dependency graph
            for dep_dict in metadata.get("dependency_graph", []):
                dep = DependencyGraph(**dep_dict)
                self.dependency_graph[dep.tool_id] = dep

            logger.info(f"📦 Loaded {len(self.tools)} tools from cache")

            # Load embeddings
            embeddings_data = await asyncio.to_thread(
                self._read_json_sync,
                EMBEDDINGS_CACHE_FILE
            )
            self.embeddings = embeddings_data.get("embeddings", {})

            logger.info(
                f"📦 Loaded {len(self.embeddings)} embeddings from cache"
            )

        except Exception as e:
            logger.error(f"Cache load failed: {e}")
            raise

    async def _save_cache(self, swagger_sources: List[str]) -> None:
        """
        Save tools and embeddings to cache.

        CRITICAL FIX: Use mode='json' to serialize Enum values properly.
        Without this, DependencySource enums cause serialization errors.
        """
        try:
            # Save manifest
            manifest = {
                "version": "2.0",
                "timestamp": datetime.utcnow().isoformat(),
                "swagger_sources": swagger_sources
            }
            await asyncio.to_thread(
                self._write_json_sync,
                MANIFEST_CACHE_FILE,
                manifest
            )
            logger.info(f"💾 Saved manifest: {len(swagger_sources)} sources")

            # Save metadata (CRITICAL: mode='json' for Enum serialization)
            metadata = {
                "version": "2.0",
                "timestamp": datetime.utcnow().isoformat(),
                "tools": [
                    tool.model_dump(mode='json')  # FIX: Serialize enums as strings
                    for tool in self.tools.values()
                ],
                "dependency_graph": [
                    dep.model_dump(mode='json')
                    for dep in self.dependency_graph.values()
                ]
            }
            await asyncio.to_thread(
                self._write_json_sync,
                METADATA_CACHE_FILE,
                metadata
            )
            logger.info(f"💾 Saved metadata: {len(self.tools)} tools")

            # Save embeddings
            embeddings_data = {
                "version": "2.0",
                "timestamp": datetime.utcnow().isoformat(),
                "embeddings": self.embeddings
            }
            await asyncio.to_thread(
                self._write_json_sync,
                EMBEDDINGS_CACHE_FILE,
                embeddings_data
            )
            logger.info(
                f"💾 Saved embeddings: {len(self.embeddings)} vectors"
            )

            # MINSKA ZONA #4: Verify all files were actually written
            logger.info("🔍 Verifying cache files...")
            for cache_file, expected_name in [
                (MANIFEST_CACHE_FILE, "manifest"),
                (METADATA_CACHE_FILE, "metadata"),
                (EMBEDDINGS_CACHE_FILE, "embeddings")
            ]:
                if not cache_file.exists():
                    raise RuntimeError(
                        f"Cache {expected_name} not created: {cache_file}"
                    )

                size = cache_file.stat().st_size
                if size == 0:
                    raise RuntimeError(
                        f"Cache {expected_name} is empty: {cache_file}"
                    )

                logger.info(
                    f"✅ {expected_name}: {cache_file.name} ({size:,} bytes)"
                )

            logger.info(
                f"✅ Cache save complete: "
                f"{len(self.tools)} tools, "
                f"{len(self.embeddings)} embeddings verified"
            )

        except Exception as e:
            logger.error(f"❌ Cache save failed: {e}", exc_info=True)
            # Re-raise to make failure visible
            raise

    # ═══════════════════════════════════════════════
    # UTILITIES
    # ═══════════════════════════════════════════════

    async def _fetch_json(self, url: str) -> Optional[Dict]:
        """Fetch JSON from URL."""
        try:
            async with httpx.AsyncClient(verify=False, timeout=30) as client:
                response = await client.get(url)
                if response.status_code == 200:
                    return response.json()
                logger.warning(
                    f"HTTP {response.status_code} for {url}"
                )
                return None
        except Exception as e:
            logger.error(f"Fetch error for {url}: {e}")
            return None

    def _read_json_sync(self, path: Path) -> Dict:
        """Synchronous JSON read."""
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_json_sync(self, path: Path, data: Dict) -> None:
        """Synchronous JSON write."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get_tool(self, operation_id: str) -> Optional[UnifiedToolDefinition]:
        """Get tool by operation ID."""
        return self.tools.get(operation_id)

    def list_tools(self) -> List[str]:
        """List all tool operation IDs."""
        return list(self.tools.keys())
