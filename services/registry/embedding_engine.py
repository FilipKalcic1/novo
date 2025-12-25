"""
Embedding Engine - Generate and manage embeddings for tool discovery.
Version: 1.0

Single responsibility: Generate embeddings using Azure OpenAI.
"""

import asyncio
import logging
import re
from typing import Dict, List, Optional

from openai import AsyncAzureOpenAI

from config import get_settings
from services.tool_contracts import (
    UnifiedToolDefinition,
    ParameterDefinition,
    DependencySource,
    DependencyGraph
)

logger = logging.getLogger(__name__)
settings = get_settings()


class EmbeddingEngine:
    """
    Manages embedding generation for semantic search.

    Responsibilities:
    - Build embedding text from tool definitions
    - Generate embeddings via Azure OpenAI
    - Build dependency graph for chaining
    """

    def __init__(self):
        """Initialize embedding engine with OpenAI client."""
        self.openai = AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION
        )
        logger.debug("EmbeddingEngine initialized")

    def build_embedding_text(
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
        Build embedding text with auto-generated PURPOSE description.

        v2.2: Infers purpose from API structure (no hardcoded translations).

        Strategy:
        1. Generate PURPOSE from: method + input params + output keys
        2. Include original description from Swagger
        3. List output fields for semantic matching

        Example:
            GET + VehicleId input + Mileage output
            → "Dohvaća kilometražu za vozilo"
        """
        # 1. Auto-generate purpose from structure
        purpose = self._generate_purpose(method, parameters, output_keys)

        # 2. Build embedding text
        parts = [
            operation_id,
            purpose,
            description if description else "",
            f"{method} {path}"
        ]

        # 3. Add output fields (human-readable)
        if output_keys:
            readable = [
                re.sub(r'([a-z])([A-Z])', r'\1 \2', k)
                for k in output_keys[:10]
            ]
            parts.append(f"Returns: {', '.join(readable)}")

        text = ". ".join(p for p in parts if p)

        if len(text) > 1500:
            text = text[:1500]

        return text

    def _generate_purpose(
        self,
        method: str,
        parameters: Dict[str, ParameterDefinition],
        output_keys: List[str]
    ) -> str:
        """
        Auto-generate purpose from API structure.

        Infers from:
        - HTTP method → action (Dohvaća/Kreira/Ažurira/Briše)
        - Input params → context (za vozilo/korisnika/period)
        - Output keys → result (kilometražu/registraciju/status)
        """
        # Action from method
        actions = {
            "GET": "Dohvaća",
            "POST": "Kreira",
            "PUT": "Ažurira",
            "PATCH": "Ažurira",
            "DELETE": "Briše"
        }
        action = actions.get(method.upper(), "Obrađuje")

        # Context from input parameters
        context = []
        has_time = False

        if parameters:
            names = [p.name.lower() for p in parameters.values()]

            if any("vehicle" in n for n in names):
                context.append("vozilo")
            if any(x in n for n in names for x in ["person", "driver", "user"]):
                context.append("korisnika")
            if any(x in n for n in names for x in ["booking", "calendar", "reservation"]):
                context.append("rezervaciju")
            if any("location" in n for n in names):
                context.append("lokaciju")

            has_time = (
                any(x in n for n in names for x in ["from", "start"]) and
                any(x in n for n in names for x in ["to", "end"])
            )

        # Result from output keys
        result = []

        if output_keys:
            keys = [k.lower() for k in output_keys]

            if any(x in k for k in keys for x in ["mileage", "km", "odometer"]):
                result.append("kilometražu")
            if any(x in k for k in keys for x in ["registration", "plate", "licence"]):
                result.append("registraciju")
            if any("expir" in k or "valid" in k for k in keys):
                result.append("datum isteka")
            if any("status" in k or "state" in k for k in keys):
                result.append("status")
            if any("available" in k or "free" in k for k in keys):
                result.append("dostupnost")
            if any("price" in k or "cost" in k for k in keys):
                result.append("cijenu")
            if any("address" in k or "location" in k for k in keys):
                result.append("adresu")
            if any("name" in k for k in keys):
                result.append("naziv")

        # Build sentence
        purpose = action

        if result:
            purpose += " " + ", ".join(result[:3])
        elif method == "GET":
            purpose += " podatke"
        elif method == "POST":
            purpose += " novi zapis"
        elif method in ("PUT", "PATCH"):
            purpose += " postojeće podatke"
        elif method == "DELETE":
            purpose += " zapis"

        if context:
            purpose += " za " + ", ".join(context[:2])

        if has_time:
            purpose += " u periodu"

        return purpose

    async def generate_embeddings(
        self,
        tools: Dict[str, UnifiedToolDefinition],
        existing_embeddings: Dict[str, List[float]]
    ) -> Dict[str, List[float]]:
        """
        Generate embeddings for tools that don't have them.

        Args:
            tools: Dict of tools by operation_id
            existing_embeddings: Already generated embeddings

        Returns:
            Updated embeddings dict
        """
        embeddings = dict(existing_embeddings)

        missing = [
            op_id for op_id in tools
            if op_id not in embeddings
        ]

        if not missing:
            logger.info("All embeddings cached")
            return embeddings

        logger.info(f"Generating {len(missing)} embeddings...")

        for op_id in missing:
            tool = tools[op_id]
            text = tool.embedding_text

            embedding = await self._get_embedding(text)
            if embedding:
                embeddings[op_id] = embedding

            await asyncio.sleep(0.05)  # Rate limiting

        logger.info(f"✅ Generated {len(missing)} embeddings")
        return embeddings

    async def _get_embedding(self, text: str) -> Optional[List[float]]:
        """Get embedding for text from Azure OpenAI."""
        try:
            response = await self.openai.embeddings.create(
                input=[text[:8000]],
                model=settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
            )
            return response.data[0].embedding
        except Exception as e:
            logger.warning(f"Embedding error: {e}")
            return None

    def build_dependency_graph(
        self,
        tools: Dict[str, UnifiedToolDefinition]
    ) -> Dict[str, DependencyGraph]:
        """
        Build dependency graph for automatic tool chaining.

        Identifies which tools can provide outputs needed by other tools.

        Args:
            tools: Dict of all tools

        Returns:
            Dict of DependencyGraph by tool_id
        """
        logger.info("Building dependency graph...")
        graph = {}

        for tool_id, tool in tools.items():
            # Find parameters that need FROM_TOOL_OUTPUT
            output_params = tool.get_output_params()
            required_outputs = list(output_params.keys())

            # Find tools that provide these outputs
            provider_tools = []
            for req_output in required_outputs:
                providers = self._find_providers(req_output, tools)
                provider_tools.extend(providers)

            if required_outputs:
                graph[tool_id] = DependencyGraph(
                    tool_id=tool_id,
                    required_outputs=required_outputs,
                    provider_tools=list(set(provider_tools))
                )

        logger.info(f"Built dependency graph: {len(graph)} tools with dependencies")
        return graph

    def _find_providers(
        self,
        output_key: str,
        tools: Dict[str, UnifiedToolDefinition]
    ) -> List[str]:
        """Find tools that provide given output key."""
        providers = []

        for tool_id, tool in tools.items():
            if output_key in tool.output_keys:
                providers.append(tool_id)
            # Case-insensitive match
            elif any(
                ok.lower() == output_key.lower()
                for ok in tool.output_keys
            ):
                providers.append(tool_id)

        return providers
