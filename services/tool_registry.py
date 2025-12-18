"""
Tool Registry
Version: 10.0

Dynamic Swagger tool management.
DEPENDS ON: schema_validator.py, config.py
"""

import asyncio
import json
import math
import re
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional, Set
from urllib.parse import urlparse

import httpx
from openai import AsyncAzureOpenAI

from config import get_settings
from services.schema_validator import SchemaValidator

logger = logging.getLogger(__name__)
settings = get_settings()

CACHE_FILE = Path.cwd() / "tool_registry_cache.json"


class ToolRegistry:
    """
    Dynamic tool registry for Swagger/OpenAPI specs.
    
    Features:
    - Parse any OpenAPI spec
    - Create OpenAI-compatible functions
    - Semantic search via embeddings
    - Persistent caching
    """
    
    BLACKLIST_PATTERNS: Set[str] = {
        "batch", "excel", "export", "import", "internal",
        "count", "odata", "searchinfo"
    }
    
    AUTO_INJECT_PARAMS: Set[str] = {
        "personid", "assignedtoid", "driverid", "tenantid",
        "createdby", "modifiedby"
    }
    
    def __init__(self, redis_client=None):
        """
        Initialize tool registry.
        
        Args:
            redis_client: Optional Redis for caching
        """
        self.redis = redis_client
        
        # OpenAI client for embeddings
        self.openai = AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION
        )
        
        # Storage
        self.tools: Dict[str, Dict[str, Any]] = {}
        self.embeddings: Dict[str, List[float]] = {}
        self.openai_functions: Dict[str, Dict] = {}
        
        # State
        self.is_ready = False
        self._load_lock = asyncio.Lock()
        
        logger.info("ToolRegistry initialized")
    
    async def load_swagger(self, source: str) -> bool:
        """
        Load tools from Swagger spec.
        
        Args:
            source: URL to swagger.json
            
        Returns:
            True if successful
        """
        async with self._load_lock:
            logger.info(f"Loading swagger: {source[:80]}")
            
            try:
                await self._load_cache()
                
                spec = await self._fetch_swagger(source)
                if not spec:
                    logger.error(f"Failed to fetch: {source}")
                    return False
                
                service = self._extract_service(source)
                tools_before = len(self.tools)
                
                await self._parse_spec(spec, service)
                
                tools_added = len(self.tools) - tools_before
                logger.info(f"Loaded {tools_added} tools from {service}")
                
                await self._generate_embeddings()
                await self._save_cache()
                
                self.is_ready = True
                return True
                
            except Exception as e:
                logger.error(f"Load swagger failed: {e}")
                return False
    
    async def _fetch_swagger(self, url: str) -> Optional[Dict]:
        """Fetch swagger spec."""
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(url)
                if response.status_code == 200:
                    return response.json()
                logger.error(f"Swagger fetch: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Swagger fetch error: {e}")
            return None
    
    def _extract_service(self, url: str) -> str:
        """Extract service name from URL."""
        for service in settings.swagger_services.keys():
            if f"/{service}/" in url.lower():
                return service
        return "unknown"
    
    async def _parse_spec(self, spec: Dict, service: str) -> None:
        """Parse OpenAPI spec."""
        paths = spec.get("paths", {})
        base_path = self._get_base_path(spec, service)
        
        for path, methods in paths.items():
            for method, operation in methods.items():
                if method.lower() not in ["get", "post", "put", "patch", "delete"]:
                    continue
                
                operation_id = self._generate_operation_id(path, method, operation)
                
                if self._is_blacklisted(operation_id, path):
                    continue
                
                full_path = f"{base_path}{path}"
                
                tool = self._create_tool(
                    operation_id, service, full_path,
                    method.upper(), operation, spec
                )
                
                if tool:
                    self.tools[operation_id] = tool
                    
                    openai_func = self._create_openai_function(tool)
                    if openai_func:
                        self.openai_functions[operation_id] = openai_func
    
    def _get_base_path(self, spec: Dict, service: str) -> str:
        """Get base path from spec."""
        if "servers" in spec and spec["servers"]:
            server_url = spec["servers"][0].get("url", "")
            if server_url.startswith("/"):
                return server_url.rstrip("/")
            elif "://" in server_url:
                return urlparse(server_url).path.rstrip("/")
        
        if "basePath" in spec:
            return spec["basePath"].rstrip("/")
        
        return f"/{service}"
    
    def _generate_operation_id(self, path: str, method: str, operation: Dict) -> str:
        """Generate operation ID."""
        if "operationId" in operation:
            return operation["operationId"]
        
        clean = re.sub(r"[^a-zA-Z0-9]", "_", path)
        clean = re.sub(r"_+", "_", clean).strip("_")
        return f"{method.lower()}_{clean}"
    
    def _is_blacklisted(self, operation_id: str, path: str) -> bool:
        """Check if operation is blacklisted."""
        combined = f"{operation_id.lower()} {path.lower()}"
        return any(p in combined for p in self.BLACKLIST_PATTERNS)
    
    def _create_tool(
        self,
        operation_id: str,
        service: str,
        path: str,
        method: str,
        operation: Dict,
        spec: Dict
    ) -> Optional[Dict[str, Any]]:
        """Create tool entry."""
        summary = operation.get("summary", "")
        description = operation.get("description", "")
        full_desc = f"{summary}. {description}".strip(". ") or operation_id
        
        parameters = {}
        required = []
        auto_inject = []
        
        # Parse parameters
        for param in operation.get("parameters", []):
            param_name = param.get("name", "")
            if not param_name:
                continue
            
            if param.get("in") == "header":
                continue
            
            if param_name.lower() in self.AUTO_INJECT_PARAMS:
                auto_inject.append(param_name)
                continue
            
            schema = param.get("schema", {})
            
            param_info = {
                "type": schema.get("type", "string"),
                "format": schema.get("format"),
                "description": param.get("description", "")[:200],
                "in": param.get("in", "query"),
                "required": param.get("required", False),
                "enum": schema.get("enum"),
                "items": schema.get("items")
            }
            
            parameters[param_name] = param_info
            
            if param.get("required"):
                required.append(param_name)
        
        # Parse request body
        if "requestBody" in operation:
            body_params = self._extract_body_params(operation["requestBody"], spec)
            for param_name, param_info in body_params.items():
                if param_name.lower() in self.AUTO_INJECT_PARAMS:
                    auto_inject.append(param_name)
                    continue
                
                param_info["in"] = "body"
                parameters[param_name] = param_info
                
                if param_info.get("required"):
                    required.append(param_name)
        
        # Build embedding text
        embedding_text = self._build_embedding_text(
            operation_id, service, path, method, full_desc, parameters
        )
        
        return {
            "operationId": operation_id,
            "service": service,
            "path": path,
            "method": method,
            "description": full_desc[:1000],
            "parameters": parameters,
            "required": required,
            "auto_inject": auto_inject,
            "embedding_text": embedding_text[:1500],
            "tags": operation.get("tags", [])
        }
    
    def _extract_body_params(self, request_body: Dict, spec: Dict) -> Dict[str, Any]:
        """Extract parameters from request body."""
        params = {}
        
        content = request_body.get("content", {})
        json_content = content.get("application/json", {})
        schema = json_content.get("schema", {})
        
        schema = self._resolve_ref(schema, spec)
        required_fields = schema.get("required", [])
        
        for prop_name, prop_schema in schema.get("properties", {}).items():
            prop_schema = self._resolve_ref(prop_schema, spec)
            
            params[prop_name] = {
                "type": prop_schema.get("type", "string"),
                "format": prop_schema.get("format"),
                "description": prop_schema.get("description", "")[:200],
                "required": prop_name in required_fields,
                "enum": prop_schema.get("enum"),
                "items": prop_schema.get("items")
            }
        
        return params
    
    def _resolve_ref(self, schema: Dict, spec: Dict) -> Dict:
        """Resolve $ref to actual schema."""
        if not isinstance(schema, dict):
            return schema
        
        if "$ref" not in schema:
            return schema
        
        ref_path = schema["$ref"]
        
        if ref_path.startswith("#/"):
            parts = ref_path[2:].split("/")
            resolved = spec
            for part in parts:
                resolved = resolved.get(part, {})
            return resolved
        
        return schema
    
    def _build_embedding_text(
        self,
        operation_id: str,
        service: str,
        path: str,
        method: str,
        description: str,
        parameters: Dict
    ) -> str:
        """Build text for embedding."""
        parts = [
            f"Operation: {operation_id}",
            f"Service: {service}",
            f"Method: {method} {path}",
            f"Description: {description}"
        ]
        
        if parameters:
            param_names = ", ".join(parameters.keys())
            parts.append(f"Parameters: {param_names}")
        
        # Semantic hints
        hints = self._generate_hints(operation_id, path, method)
        if hints:
            parts.append(f"Use for: {', '.join(hints)}")
        
        return ". ".join(parts)
    
    def _generate_hints(self, operation_id: str, path: str, method: str) -> List[str]:
        """Generate semantic hints."""
        hints = []
        combined = f"{operation_id} {path}".lower()
        
        if "available" in combined or "calendar" in combined:
            hints.extend(["rezervacija", "booking", "najam", "slobodna vozila"])
        
        if "masterdata" in combined or ("vehicle" in combined and method == "GET"):
            hints.extend(["podaci o vozilu", "kilometraža", "registracija"])
        
        if "case" in combined or "damage" in combined:
            hints.extend(["šteta", "kvar", "prijava", "nesreća"])
        
        if "email" in combined:
            hints.extend(["email", "pošalji", "obavijest"])
        
        if "person" in combined:
            hints.extend(["osoba", "korisnik", "vozač"])
        
        return hints
    
    def _create_openai_function(self, tool: Dict[str, Any]) -> Optional[Dict]:
        """Create OpenAI function from tool."""
        parameters = {}
        required = []
        
        for param_name, param_info in tool.get("parameters", {}).items():
            param_schema = {
                "type": param_info.get("type", "string"),
                "description": param_info.get("description", param_name)
            }
            
            # Format hint
            if param_info.get("format") == "date-time":
                param_schema["description"] += " (ISO 8601: YYYY-MM-DDTHH:MM:SS)"
            
            if param_info.get("enum"):
                param_schema["enum"] = param_info["enum"]
            
            # Handle arrays
            if param_info.get("type") == "array":
                items = param_info.get("items", {})
                if items:
                    param_schema["items"] = SchemaValidator.validate_and_fix(items)
                else:
                    param_schema["items"] = {"type": "string"}
            
            parameters[param_name] = param_schema
            
            if param_info.get("required"):
                required.append(param_name)
        
        return SchemaValidator.create_openai_function(
            name=tool["operationId"],
            description=tool["description"],
            parameters=parameters,
            required=required
        )
    
    # === EMBEDDINGS ===
    
    async def _generate_embeddings(self) -> None:
        """Generate embeddings for new tools."""
        missing = [op for op in self.tools if op not in self.embeddings]
        
        if not missing:
            logger.info(f"All {len(self.embeddings)} embeddings cached")
            return
        
        logger.info(f"Generating {len(missing)} embeddings...")
        
        for op_id in missing:
            tool = self.tools.get(op_id)
            if not tool:
                continue
            
            text = tool.get("embedding_text", tool["description"])
            embedding = await self._get_embedding(text)
            
            if embedding:
                self.embeddings[op_id] = embedding
            
            await asyncio.sleep(0.05)
        
        logger.info(f"Generated {len(missing)} embeddings")
    
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
    
    # === SEARCH ===
    
    async def find_relevant_tools(
        self,
        query: str,
        top_k: int = 5,
        threshold: float = None
    ) -> List[Dict]:
        """
        Find relevant tools via semantic search.
        
        Args:
            query: User query
            top_k: Number of tools to return
            threshold: Minimum similarity
            
        Returns:
            List of OpenAI function definitions
        """
        if not self.is_ready:
            logger.warning("Registry not ready")
            return []
        
        threshold = threshold or settings.SIMILARITY_THRESHOLD
        
        query_embedding = await self._get_embedding(query)
        if not query_embedding:
            return self._fallback_search(query, top_k)
        
        scored = []
        for op_id, tool_embedding in self.embeddings.items():
            similarity = self._cosine_similarity(query_embedding, tool_embedding)
            
            if similarity >= threshold:
                scored.append((similarity, op_id))
        
        scored.sort(key=lambda x: x[0], reverse=True)
        
        if scored:
            top = [(f"{s:.3f}", op) for s, op in scored[:5]]
            logger.info(f"Top matches: {top}")
        
        results = []
        for _, op_id in scored[:top_k]:
            if op_id in self.openai_functions:
                results.append(self.openai_functions[op_id])
        
        return results
    
    def _fallback_search(self, query: str, top_k: int) -> List[Dict]:
        """Fallback keyword search."""
        query_lower = query.lower()
        matches = []
        
        for op_id, tool in self.tools.items():
            text = f"{tool['description']} {tool['path']}".lower()
            score = sum(1 for word in query_lower.split() if word in text)
            
            if score > 0:
                matches.append((score, op_id))
        
        matches.sort(key=lambda x: x[0], reverse=True)
        
        results = []
        for _, op_id in matches[:top_k]:
            if op_id in self.openai_functions:
                results.append(self.openai_functions[op_id])
        
        return results
    
    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """Calculate cosine similarity."""
        if not a or not b or len(a) != len(b):
            return 0.0
        
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        
        if norm_a == 0 or norm_b == 0:
            return 0.0
        
        return dot / (norm_a * norm_b)
    
    # === ACCESS ===
    
    def get_tool(self, operation_id: str) -> Optional[Dict[str, Any]]:
        """Get tool by operation ID."""
        return self.tools.get(operation_id)
    
    def get_openai_function(self, operation_id: str) -> Optional[Dict]:
        """Get OpenAI function by operation ID."""
        return self.openai_functions.get(operation_id)
    
    def list_tools(self) -> List[str]:
        """List all tool operation IDs."""
        return list(self.tools.keys())
    
    # === CACHING ===
    
    async def _load_cache(self) -> None:
        """Load cache from file."""
        if not CACHE_FILE.exists():
            return
        
        try:
            with open(CACHE_FILE, "r") as f:
                data = json.load(f)
            
            self.embeddings = data.get("embeddings", {})
            logger.info(f"Loaded {len(self.embeddings)} embeddings from cache")
        except Exception as e:
            logger.warning(f"Cache load failed: {e}")
    
    async def _save_cache(self) -> None:
        """Save cache to file."""
        try:
            data = {
                "version": "10.0",
                "timestamp": datetime.utcnow().isoformat(),
                "embeddings": self.embeddings
            }
            
            with open(CACHE_FILE, "w") as f:
                json.dump(data, f)
            
            logger.debug(f"Saved {len(self.embeddings)} embeddings")
        except Exception as e:
            logger.warning(f"Cache save failed: {e}")
