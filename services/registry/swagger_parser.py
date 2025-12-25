"""
Swagger Parser - Parse OpenAPI/Swagger specs into UnifiedToolDefinition.
Version: 1.0

Single responsibility: Parse Swagger JSON specs and create tool definitions.
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import httpx

from services.tool_contracts import (
    UnifiedToolDefinition,
    ParameterDefinition,
    DependencySource
)

logger = logging.getLogger(__name__)

# Config file path
CONFIG_PATH = Path.cwd() / "config" / "context_param_schemas.json"


class SwaggerParser:
    """
    Parses OpenAPI/Swagger specifications into UnifiedToolDefinition objects.

    Responsibilities:
    - Fetch Swagger specs from URLs
    - Parse paths, methods, parameters
    - Classify context parameters
    - Infer output keys from response schemas
    """

    # Blacklist patterns for operations to skip
    BLACKLIST_PATTERNS: Set[str] = {
        "batch", "excel", "export", "import", "internal",
        "count", "odata", "searchinfo", "swagger", "health"
    }

    def __init__(self):
        """Initialize parser with context parameter schemas."""
        self.context_param_patterns: Dict[str, Dict] = {}
        self.context_param_fallback: Dict[str, str] = {}
        self.compiled_name_patterns: Dict[str, List[re.Pattern]] = {}
        self._load_context_param_schemas()

    def _load_context_param_schemas(self) -> None:
        """Load context parameter classification schemas from config."""
        if not CONFIG_PATH.exists():
            logger.warning(f"Config not found: {CONFIG_PATH}. Using defaults.")
            self._init_default_patterns()
            return

        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)

            for context_type, rules in config.get("context_types", {}).items():
                classification_rules = rules.get("classification_rules", {})

                self.context_param_patterns[context_type] = {
                    "schema_formats": rules.get("schema_hints", {}).get("formats", []),
                    "description_keywords": classification_rules.get("description_keywords", []),
                    "type_hints": rules.get("schema_hints", {}).get("types", ["string"]),
                }

                name_patterns = classification_rules.get("name_patterns", [])
                self.compiled_name_patterns[context_type] = [
                    re.compile(pattern) for pattern in name_patterns
                ]

                for name in rules.get("fallback_names", []):
                    self.context_param_fallback[name.lower()] = context_type

            logger.info(f"Loaded {len(config.get('context_types', {}))} context types")

        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            self._init_default_patterns()

    def _init_default_patterns(self) -> None:
        """Initialize with hardcoded defaults."""
        self.context_param_patterns = {
            "person_id": {
                "schema_formats": ["uuid", "guid"],
                "description_keywords": ["person", "user", "driver", "employee"],
                "type_hints": ["string"],
            },
            "vehicle_id": {
                "schema_formats": ["uuid", "guid"],
                "description_keywords": ["vehicle", "car", "asset"],
                "type_hints": ["string"],
            },
            "tenant_id": {
                "schema_formats": ["uuid", "guid"],
                "description_keywords": ["tenant", "organization"],
                "type_hints": ["string"],
            },
        }
        self.context_param_fallback = {
            "personid": "person_id",
            "person_id": "person_id",
            "userid": "person_id",
            "driverid": "person_id",
            "tenantid": "tenant_id",
        }

    async def fetch_spec(self, url: str) -> Optional[Dict]:
        """Fetch Swagger spec from URL."""
        try:
            async with httpx.AsyncClient(verify=False, timeout=30) as client:
                response = await client.get(url)
                if response.status_code == 200:
                    return response.json()
                logger.warning(f"HTTP {response.status_code} for {url}")
                return None
        except Exception as e:
            logger.error(f"Fetch error for {url}: {e}")
            return None

    async def parse_spec(
        self,
        url: str,
        build_embedding_text_fn
    ) -> List[UnifiedToolDefinition]:
        """
        Parse Swagger spec and return list of tool definitions.

        Args:
            url: Swagger spec URL
            build_embedding_text_fn: Function to build embedding text

        Returns:
            List of UnifiedToolDefinition objects
        """
        spec = await self.fetch_spec(url)
        if not spec:
            logger.warning(f"Empty spec: {url}")
            return []

        service_name = self._extract_service_name(url)
        service_url = self._extract_base_url(spec)

        tools = []
        paths = spec.get("paths", {})

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
                        spec=spec,
                        build_embedding_text_fn=build_embedding_text_fn
                    )
                    if tool:
                        tools.append(tool)
                except Exception as e:
                    logger.debug(f"Skipped {method} {path}: {e}")

        logger.info(f"✅ {service_name}: {len(tools)} operations")
        return tools

    def _parse_operation(
        self,
        service_name: str,
        service_url: str,
        path: str,
        method: str,
        operation: Dict,
        spec: Dict,
        build_embedding_text_fn
    ) -> Optional[UnifiedToolDefinition]:
        """Parse single OpenAPI operation into UnifiedToolDefinition."""
        operation_id = operation.get("operationId")
        if not operation_id:
            operation_id = self._generate_operation_id(path, method)

        if self._is_blacklisted(operation_id, path):
            return None

        summary = operation.get("summary", "")
        description = operation.get("description", "")
        full_desc = f"{summary}. {description}".strip(". ")

        # Parse parameters
        parameters = {}
        required_params = []

        for param in operation.get("parameters", []):
            param_def = self._parse_parameter(param)
            if param_def:
                parameters[param_def.name] = param_def
                if param_def.required:
                    required_params.append(param_def.name)

        # Parse request body
        if "requestBody" in operation:
            body_params = self._parse_request_body(operation["requestBody"], spec)
            parameters.update(body_params)
            for name, param_def in body_params.items():
                if param_def.required:
                    required_params.append(name)

        # Infer output keys
        output_keys = self._infer_output_keys(operation, spec)

        # Build embedding text
        embedding_text = build_embedding_text_fn(
            operation_id, service_name, path, method,
            full_desc, parameters, output_keys
        )

        # Extract swagger_name
        swagger_name = self._extract_swagger_name(service_url)

        return UnifiedToolDefinition(
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

    def _classify_context_parameter(
        self,
        param_name: str,
        param_type: str,
        param_format: Optional[str],
        description: str
    ) -> Tuple[Optional[str], bool]:
        """Classify parameter as context param using schema metadata."""
        description_lower = description.lower()

        for context_key, patterns in self.context_param_patterns.items():
            score = 0

            if param_format and param_format.lower() in patterns["schema_formats"]:
                score += 2

            if any(kw in description_lower for kw in patterns["description_keywords"]):
                score += 3

            if param_type in patterns["type_hints"]:
                score += 1

            if score >= 3:
                return context_key, True

        param_lower = param_name.lower()
        if param_lower in self.context_param_fallback:
            return self.context_param_fallback[param_lower], True

        return None, False

    def _parse_parameter(self, param: Dict) -> Optional[ParameterDefinition]:
        """Parse OpenAPI parameter into ParameterDefinition."""
        param_name = param.get("name")
        if not param_name:
            return None

        location = param.get("in", "query")
        if location == "header":
            return None

        schema = param.get("schema", {})
        param_type = schema.get("type", "string")
        param_format = schema.get("format")
        description = param.get("description", "")

        context_key, is_context = self._classify_context_parameter(
            param_name, param_type, param_format, description
        )

        dependency_source = (
            DependencySource.FROM_CONTEXT if is_context
            else DependencySource.FROM_USER
        )

        return ParameterDefinition(
            name=param_name,
            param_type=param_type,
            format=param_format,
            description=description[:200],
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

            prop_type = prop_schema.get("type", "string")
            prop_format = prop_schema.get("format")
            prop_description = prop_schema.get("description", "")

            context_key, is_context = self._classify_context_parameter(
                prop_name, prop_type, prop_format, prop_description
            )

            dependency_source = (
                DependencySource.FROM_CONTEXT if is_context
                else DependencySource.FROM_USER
            )

            params[prop_name] = ParameterDefinition(
                name=prop_name,
                param_type=prop_type,
                format=prop_format,
                description=prop_description[:200],
                required=prop_name in required_fields,
                location="body",
                dependency_source=dependency_source,
                context_key=context_key,
                enum_values=prop_schema.get("enum"),
                items_type=prop_schema.get("items", {}).get("type")
            )

        return params

    def _infer_output_keys(self, operation: Dict, spec: Dict) -> List[str]:
        """Infer output keys from response schema."""
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

        if schema.get("type") == "array":
            items_schema = schema.get("items", {})
            schema = self._resolve_ref(items_schema, spec)

        properties = schema.get("properties", {})

        # Check for wrapper keys
        for wrapper_key in ["Items", "items", "Data", "data", "results", "value"]:
            if wrapper_key in properties:
                wrapper_schema = self._resolve_ref(properties[wrapper_key], spec)
                if wrapper_schema.get("type") == "array":
                    items_schema = wrapper_schema.get("items", {})
                    items_schema = self._resolve_ref(items_schema, spec)
                    properties = items_schema.get("properties", {})
                    break

        all_keys = list(properties.keys())

        useful_patterns = [
            "id", "ID", "Id", "uuid", "UUID", "Uuid",
            "vin", "VIN", "Vin",
            "plate", "Plate", "LicencePlate", "RegistrationNumber",
            "code", "Code", "number", "Number", "name", "Name",
            "externalId", "ExternalId", "referenceId", "ReferenceId",
        ]

        for key in all_keys:
            if any(pattern in key for pattern in useful_patterns):
                output_keys.append(key)

        for key in all_keys:
            if key.endswith("Id") or key.endswith("ID") or key.endswith("UUID"):
                if key not in output_keys:
                    output_keys.append(key)

        return output_keys[:15]

    def _resolve_ref(self, schema: Dict, spec: Dict) -> Dict:
        """Resolve $ref to actual schema."""
        if not isinstance(schema, dict) or "$ref" not in schema:
            return schema

        ref_path = schema["$ref"]
        if not ref_path.startswith("#/"):
            return schema

        parts = ref_path[2:].split("/")
        resolved = spec
        for part in parts:
            resolved = resolved.get(part, {})

        return resolved

    def _extract_service_name(self, url: str) -> str:
        """Extract service name from Swagger URL."""
        parts = url.split("/")
        for i, part in enumerate(parts):
            if part == "swagger" and i > 0:
                return parts[i - 1]
        return "unknown"

    def _extract_base_url(self, spec: Dict) -> str:
        """Extract base URL from OpenAPI spec."""
        if "servers" in spec and spec["servers"]:
            servers = spec["servers"]

            for server in servers:
                url = server.get("url", "")
                description = server.get("description", "").lower()

                if any(kw in description for kw in ["dev", "test", "local", "staging"]):
                    continue

                if url.startswith("http://") or url.startswith("https://"):
                    return url.rstrip("/")

            first_url = servers[0].get("url", "")
            if first_url:
                return first_url.rstrip("/")

        if "basePath" in spec:
            base_path = spec["basePath"]
            if not base_path.startswith("http"):
                host = spec.get("host", "")
                schemes = spec.get("schemes", ["https"])
                if host:
                    return f"{schemes[0]}://{host}{base_path}".rstrip("/")
            return base_path.rstrip("/")

        return ""

    def _extract_swagger_name(self, service_url: str) -> str:
        """Extract swagger name from service URL."""
        if not service_url:
            return ""

        clean_url = service_url.lstrip("/")
        if not clean_url.startswith("http"):
            return clean_url

        parsed = urlparse(clean_url)
        path_parts = [p for p in parsed.path.split("/") if p]
        return path_parts[0] if path_parts else ""

    def _generate_operation_id(self, path: str, method: str) -> str:
        """Generate operation ID from path and method."""
        clean = re.sub(r"[^a-zA-Z0-9]", "_", path)
        clean = re.sub(r"_+", "_", clean).strip("_")
        return f"{method.lower()}_{clean}"

    def _is_blacklisted(self, operation_id: str, path: str) -> bool:
        """Check if operation should be blacklisted."""
        combined = f"{operation_id.lower()} {path.lower()}"
        return any(pattern in combined for pattern in self.BLACKLIST_PATTERNS)
