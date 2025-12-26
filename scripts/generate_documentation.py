"""
Documentation Generator - Automated tool documentation using LLM.
Version: 1.0

Generates:
- config/tool_categories.json - Tool categorization (15-20 categories)
- config/tool_documentation.json - Detailed docs for each tool
- data/training_queries.json - 500+ query→tool examples
- config/knowledge_graph.json - Entity relationships

Usage:
    python -m scripts.generate_documentation

Estimated time: ~40 minutes for 900+ tools
Estimated cost: ~$5-10 (GPT-4)
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import AsyncAzureOpenAI, RateLimitError
from config import get_settings
from services.tool_contracts import UnifiedToolDefinition

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
settings = get_settings()

# Output directories
CONFIG_DIR = Path(__file__).parent.parent / "config"
DATA_DIR = Path(__file__).parent.parent / "data"

# Batch sizes for LLM calls
CATEGORY_BATCH_SIZE = 50  # Tools per categorization request
DOC_BATCH_SIZE = 10       # Tools per documentation request
TRAINING_EXAMPLES_PER_CATEGORY = 25


class DocumentationGenerator:
    """
    Generates rich documentation for all tools using LLM.
    Runs ONCE to create config files, not on every request.
    """

    def __init__(self):
        """Initialize with Azure OpenAI client."""
        self.client = AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            max_retries=3,
            timeout=120.0
        )
        self.model = settings.AZURE_OPENAI_DEPLOYMENT_NAME

        # Statistics
        self.stats = {
            "llm_calls": 0,
            "tokens_used": 0,
            "errors": 0,
            "start_time": None,
            "end_time": None
        }

    async def generate_all(self, tools: Dict[str, UnifiedToolDefinition]) -> Dict[str, Any]:
        """
        Main entry point - generates all documentation.

        Args:
            tools: Dictionary of operation_id -> UnifiedToolDefinition

        Returns:
            Dict with categories, documentation, training_data, knowledge_graph
        """
        self.stats["start_time"] = datetime.now()
        logger.info(f"Starting documentation generation for {len(tools)} tools")

        # Ensure output directories exist
        CONFIG_DIR.mkdir(exist_ok=True)
        DATA_DIR.mkdir(exist_ok=True)

        # Step 1: Categorize all tools
        logger.info("=" * 60)
        logger.info("STEP 1: Categorizing tools...")
        categories = await self._categorize_tools(tools)
        self._save_json(CONFIG_DIR / "tool_categories.json", categories)
        logger.info(f"✅ Created {len(categories.get('categories', {}))} categories")

        # Step 2: Generate documentation for each tool
        logger.info("=" * 60)
        logger.info("STEP 2: Generating tool documentation...")
        documentation = await self._generate_documentation(tools, categories)
        self._save_json(CONFIG_DIR / "tool_documentation.json", documentation)
        logger.info(f"✅ Documented {len(documentation)} tools")

        # Step 3: Generate training examples
        logger.info("=" * 60)
        logger.info("STEP 3: Generating training examples...")
        training_data = await self._generate_training_examples(tools, categories)
        self._save_json(DATA_DIR / "training_queries.json", training_data)
        logger.info(f"✅ Generated {len(training_data.get('examples', []))} training examples")

        # Step 4: Build knowledge graph
        logger.info("=" * 60)
        logger.info("STEP 4: Building knowledge graph...")
        knowledge_graph = await self._build_knowledge_graph(tools, categories)
        self._save_json(CONFIG_DIR / "knowledge_graph.json", knowledge_graph)
        logger.info("✅ Knowledge graph created")

        self.stats["end_time"] = datetime.now()
        duration = (self.stats["end_time"] - self.stats["start_time"]).total_seconds()

        logger.info("=" * 60)
        logger.info("GENERATION COMPLETE")
        logger.info(f"Duration: {duration:.1f} seconds ({duration/60:.1f} minutes)")
        logger.info(f"LLM calls: {self.stats['llm_calls']}")
        logger.info(f"Tokens used: {self.stats['tokens_used']}")
        logger.info(f"Errors: {self.stats['errors']}")
        logger.info("=" * 60)

        return {
            "categories": categories,
            "documentation": documentation,
            "training_data": training_data,
            "knowledge_graph": knowledge_graph,
            "stats": self.stats
        }

    async def _categorize_tools(self, tools: Dict[str, UnifiedToolDefinition]) -> Dict:
        """
        Use LLM to categorize all tools into 15-20 categories.

        Batches tools into chunks for efficient processing.
        """
        # Prepare tool summaries
        tool_summaries = []
        for op_id, tool in tools.items():
            summary = {
                "id": op_id,
                "method": tool.method,
                "path": tool.path,
                "description": tool.description[:200] if tool.description else "",
                "params": [p.name for p in tool.parameters.values()][:5],
                "outputs": tool.output_keys[:5] if tool.output_keys else []
            }
            tool_summaries.append(summary)

        # Batch categorization
        all_suggestions = []
        batches = [tool_summaries[i:i + CATEGORY_BATCH_SIZE]
                   for i in range(0, len(tool_summaries), CATEGORY_BATCH_SIZE)]

        for batch_num, batch in enumerate(batches):
            logger.info(f"Categorizing batch {batch_num + 1}/{len(batches)} ({len(batch)} tools)")

            prompt = f"""Analiziraj ove API endpointe i predloži kategorije za svaki.

ENDPOINTI:
{json.dumps(batch, ensure_ascii=False, indent=2)}

Za svaki endpoint predloži jednu kategoriju (engleski, snake_case).
Kategorije trebaju biti:
- Opisne i razumljive (npr. "vehicle_info", "booking_management", "mileage_tracking")
- Grupirane po funkcionalnosti, ne po tehničkim detaljima
- Maksimalno 20 različitih kategorija za cijeli sustav

Vrati JSON array:
[
  {{"id": "operationId", "category": "category_name"}},
  ...
]

Samo JSON, bez objašnjenja."""

            result = await self._call_llm(prompt)
            if result:
                try:
                    parsed = json.loads(result)
                    all_suggestions.extend(parsed)
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse categorization batch: {e}")
                    self.stats["errors"] += 1

        # Aggregate categories
        category_tools = {}
        tool_to_category = {}

        for suggestion in all_suggestions:
            cat = suggestion.get("category", "uncategorized")
            tool_id = suggestion.get("id", "")

            if cat not in category_tools:
                category_tools[cat] = []
            category_tools[cat].append(tool_id)
            tool_to_category[tool_id] = cat

        # Generate category metadata
        categories_result = {
            "categories": {},
            "tool_to_category": tool_to_category,
            "generated_at": datetime.now().isoformat()
        }

        for cat_name, cat_tools in category_tools.items():
            # Get sample tools for description
            sample_tools = cat_tools[:5]
            sample_info = []
            for tid in sample_tools:
                if tid in tools:
                    t = tools[tid]
                    sample_info.append(f"{tid}: {t.description[:50] if t.description else 'No desc'}")

            prompt = f"""Generiraj opis za kategoriju API endpointa.

Kategorija: {cat_name}
Broj alata: {len(cat_tools)}
Primjeri:
{chr(10).join(sample_info)}

Vrati JSON:
{{
  "name": "{cat_name}",
  "description_hr": "Opis na hrvatskom (1-2 rečenice)",
  "description_en": "English description (1-2 sentences)",
  "keywords_hr": ["ključna", "riječ"],
  "keywords_en": ["key", "words"],
  "typical_intents": ["WHAT_USER_WANTS_1", "WHAT_USER_WANTS_2"]
}}

Samo JSON."""

            meta_result = await self._call_llm(prompt)
            if meta_result:
                try:
                    meta = json.loads(meta_result)
                    meta["tools"] = cat_tools
                    meta["tool_count"] = len(cat_tools)
                    categories_result["categories"][cat_name] = meta
                except json.JSONDecodeError:
                    # Fallback - basic category
                    categories_result["categories"][cat_name] = {
                        "name": cat_name,
                        "description_hr": cat_name.replace("_", " ").title(),
                        "description_en": cat_name.replace("_", " ").title(),
                        "keywords_hr": [],
                        "keywords_en": [],
                        "typical_intents": [],
                        "tools": cat_tools,
                        "tool_count": len(cat_tools)
                    }

        return categories_result

    async def _generate_documentation(
        self,
        tools: Dict[str, UnifiedToolDefinition],
        categories: Dict
    ) -> Dict[str, Any]:
        """
        Generate detailed documentation for each tool.

        Batches tools for efficient processing.
        """
        documentation = {}
        tool_list = list(tools.items())
        batches = [tool_list[i:i + DOC_BATCH_SIZE]
                   for i in range(0, len(tool_list), DOC_BATCH_SIZE)]

        for batch_num, batch in enumerate(batches):
            logger.info(f"Documenting batch {batch_num + 1}/{len(batches)} ({len(batch)} tools)")

            # Prepare batch info
            batch_info = []
            for op_id, tool in batch:
                category = categories.get("tool_to_category", {}).get(op_id, "uncategorized")
                info = {
                    "id": op_id,
                    "method": tool.method,
                    "path": tool.path,
                    "description": tool.description or "",
                    "category": category,
                    "parameters": {
                        name: {
                            "type": p.param_type,
                            "required": p.required,
                            "description": p.description or ""
                        }
                        for name, p in tool.parameters.items()
                    },
                    "output_fields": tool.output_keys[:10] if tool.output_keys else []
                }
                batch_info.append(info)

            prompt = f"""Generiraj detaljnu dokumentaciju za ove API endpointe.

ENDPOINTI:
{json.dumps(batch_info, ensure_ascii=False, indent=2)}

Za SVAKI endpoint vrati:
{{
  "operation_id": "...",
  "purpose": "Zašto ovaj endpoint postoji (hrvatski, 1 rečenica)",
  "when_to_use": ["Kada koristiti 1", "Kada koristiti 2"],
  "when_not_to_use": ["Kada NE koristiti"],
  "prerequisites": ["Što mora biti ispunjeno"],
  "output_fields_explained": {{"FieldName": "Što znači ovo polje"}},
  "common_errors": {{"400": "Opis", "403": "Opis", "404": "Opis"}},
  "next_steps": ["Što napraviti nakon uspješnog poziva"],
  "related_tools": ["povezani_tool_1", "povezani_tool_2"],
  "example_queries_hr": ["primjer pitanja 1", "primjer pitanja 2", "primjer 3"]
}}

Vrati JSON array svih endpointa. Samo JSON."""

            result = await self._call_llm(prompt, max_tokens=4000)
            if result:
                try:
                    docs = json.loads(result)
                    for doc in docs:
                        op_id = doc.get("operation_id", "")
                        if op_id:
                            documentation[op_id] = doc
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse documentation batch: {e}")
                    self.stats["errors"] += 1

        return documentation

    async def _generate_training_examples(
        self,
        tools: Dict[str, UnifiedToolDefinition],
        categories: Dict
    ) -> Dict[str, Any]:
        """
        Generate query→tool training examples for each category.
        """
        training_data = {
            "examples": [],
            "generated_at": datetime.now().isoformat(),
            "version": "1.0"
        }

        category_list = list(categories.get("categories", {}).items())

        for cat_num, (cat_name, cat_info) in enumerate(category_list):
            logger.info(f"Generating examples for category {cat_num + 1}/{len(category_list)}: {cat_name}")

            # Get tools in this category
            cat_tools = cat_info.get("tools", [])[:10]  # Limit to 10 for prompt size

            tool_details = []
            for tid in cat_tools:
                if tid in tools:
                    t = tools[tid]
                    tool_details.append({
                        "id": tid,
                        "method": t.method,
                        "description": t.description[:100] if t.description else "",
                        "outputs": t.output_keys[:5] if t.output_keys else []
                    })

            if not tool_details:
                continue

            prompt = f"""Generiraj {TRAINING_EXAMPLES_PER_CATEGORY} primjera pitanja korisnika za ovu kategoriju API alata.

KATEGORIJA: {cat_name}
OPIS: {cat_info.get('description_hr', '')}

DOSTUPNI ALATI:
{json.dumps(tool_details, ensure_ascii=False, indent=2)}

Za svaki primjer:
- query: Pitanje na HRVATSKOM (različite formulacije! formalno i neformalno)
- intent: Što korisnik želi (UPPERCASE, engleski)
- primary_tool: Najbolji tool za ovo pitanje
- alternative_tools: Backup opcije ako primarni ne radi
- extract_fields: Koja polja izvući iz response-a
- response_template: Kratki predložak odgovora

VAŽNO:
- Uključi RAZLIČITE formulacije (formalno, neformalno, skraćeno)
- Uključi greške u pisanju (npr "kolko" umjesto "koliko")
- Uključi sinonime i različite načine postavljanja istog pitanja

Vrati JSON array:
[
  {{
    "query": "kolika mi je kilometraza",
    "intent": "GET_MILEAGE",
    "primary_tool": "get_MasterData",
    "alternative_tools": ["get_Mileage"],
    "extract_fields": ["Mileage"],
    "response_template": "📏 Kilometraža: {{Mileage}} km",
    "category": "{cat_name}"
  }},
  ...
]

Samo JSON array."""

            result = await self._call_llm(prompt, max_tokens=3000)
            if result:
                try:
                    examples = json.loads(result)
                    for ex in examples:
                        ex["category"] = cat_name
                    training_data["examples"].extend(examples)
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse training examples for {cat_name}: {e}")
                    self.stats["errors"] += 1

        return training_data

    async def _build_knowledge_graph(
        self,
        tools: Dict[str, UnifiedToolDefinition],
        categories: Dict
    ) -> Dict[str, Any]:
        """
        Build knowledge graph of entity relationships.
        """
        # Extract entity types from tools
        entity_hints = set()
        for op_id, tool in tools.items():
            # From parameters
            for param_name in tool.parameters.keys():
                if param_name.endswith("Id"):
                    entity = param_name[:-2]
                    entity_hints.add(entity)
            # From output keys
            for key in (tool.output_keys or []):
                if key.endswith("Id"):
                    entity = key[:-2]
                    entity_hints.add(entity)

        prompt = f"""Na temelju ovih entiteta iz API-ja, izgradi knowledge graph odnosa.

DETEKTIRANI ENTITETI:
{sorted(entity_hints)}

Za svaki glavni entitet definiraj:
- properties: Glavna svojstva
- relationships: Odnosi s drugim entitetima (format: relation_name -> TargetEntity)
- constraints: Poslovna pravila

Fokusiraj se na glavne entitete: Person, Vehicle, Booking, Tenant, Case, Registration

Vrati JSON:
{{
  "entities": {{
    "Person": {{
      "properties": ["PersonId", "Name", "Phone", "Email"],
      "relationships": {{
        "drives": "Vehicle",
        "has_bookings": "Booking",
        "works_for": "Tenant"
      }},
      "description": "Korisnik sustava (vozač ili admin)"
    }},
    ...
  }},
  "constraints": [
    {{"name": "booking_no_overlap", "description": "Vozilo ne može imati dvije rezervacije u isto vrijeme"}},
    ...
  ],
  "entity_resolution": {{
    "my_vehicle": "Vehicle assigned to current Person",
    "moje vozilo": "Vehicle assigned to current Person",
    ...
  }}
}}

Samo JSON."""

        result = await self._call_llm(prompt, max_tokens=2000)

        if result:
            try:
                knowledge_graph = json.loads(result)
                knowledge_graph["generated_at"] = datetime.now().isoformat()
                knowledge_graph["detected_entities"] = sorted(entity_hints)
                return knowledge_graph
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse knowledge graph: {e}")
                self.stats["errors"] += 1

        # Fallback - basic structure
        return {
            "entities": {
                "Person": {
                    "properties": ["PersonId", "Name", "Phone", "Email"],
                    "relationships": {"drives": "Vehicle", "has_bookings": "Booking"},
                    "description": "Korisnik sustava"
                },
                "Vehicle": {
                    "properties": ["VehicleId", "LicencePlate", "Name", "Mileage"],
                    "relationships": {"assigned_to": "Person", "has_bookings": "Booking"},
                    "description": "Vozilo u floti"
                },
                "Booking": {
                    "properties": ["BookingId", "FromTime", "ToTime", "Status"],
                    "relationships": {"for_vehicle": "Vehicle", "booked_by": "Person"},
                    "description": "Rezervacija vozila"
                }
            },
            "constraints": [],
            "entity_resolution": {},
            "detected_entities": sorted(entity_hints),
            "generated_at": datetime.now().isoformat()
        }

    async def _call_llm(self, prompt: str, max_tokens: int = 2000) -> Optional[str]:
        """
        Make LLM call with retry logic.
        """
        self.stats["llm_calls"] += 1

        messages = [
            {"role": "system", "content": "Ti si stručnjak za API dokumentaciju. Odgovaraj SAMO s validnim JSON-om."},
            {"role": "user", "content": prompt}
        ]

        for attempt in range(3):
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.3,  # Lower for more consistent output
                    max_tokens=max_tokens
                )

                if response.usage:
                    self.stats["tokens_used"] += response.usage.total_tokens

                if response.choices:
                    content = response.choices[0].message.content
                    # Clean up common JSON issues
                    content = content.strip()
                    if content.startswith("```json"):
                        content = content[7:]
                    if content.startswith("```"):
                        content = content[3:]
                    if content.endswith("```"):
                        content = content[:-3]
                    return content.strip()

            except RateLimitError:
                wait_time = (2 ** attempt) * 5  # 5, 10, 20 seconds
                logger.warning(f"Rate limit hit, waiting {wait_time}s...")
                await asyncio.sleep(wait_time)
                continue

            except Exception as e:
                logger.error(f"LLM call error: {e}")
                self.stats["errors"] += 1
                await asyncio.sleep(2)
                continue

        return None

    def _save_json(self, path: Path, data: Any):
        """Save data to JSON file with pretty printing."""
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved: {path}")


async def load_tools_from_registry() -> Dict[str, UnifiedToolDefinition]:
    """
    Load tools from the registry (requires full initialization).
    """
    from services.registry import ToolRegistry
    from services.context_service import ContextService

    logger.info("Initializing registry...")

    # Create minimal context service for Redis
    try:
        context = ContextService()
        redis = context.redis
    except Exception:
        redis = None
        logger.warning("Redis not available, continuing without cache")

    registry = ToolRegistry(redis_client=redis)

    # Get swagger sources from settings
    swagger_sources = settings.swagger_sources

    if not swagger_sources:
        # Fallback to main API
        swagger_sources = [
            f"{settings.MOBILITY_API_URL.rstrip('/')}/swagger/v1/swagger.json"
        ]

    success = await registry.initialize(swagger_sources)

    if not success:
        raise RuntimeError("Failed to initialize registry")

    logger.info(f"Loaded {len(registry.tools)} tools from registry")
    return registry.tools


async def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("DOCUMENTATION GENERATOR")
    logger.info("=" * 60)

    # Check if we should skip loading (for testing with cached tools)
    if "--dry-run" in sys.argv:
        logger.info("Dry run mode - checking configuration only")
        logger.info(f"Azure endpoint: {settings.AZURE_OPENAI_ENDPOINT}")
        logger.info(f"Model: {settings.AZURE_OPENAI_DEPLOYMENT_NAME}")
        logger.info(f"Output dirs: {CONFIG_DIR}, {DATA_DIR}")
        return

    try:
        # Load tools
        tools = await load_tools_from_registry()

        # Generate documentation
        generator = DocumentationGenerator()
        result = await generator.generate_all(tools)

        logger.info("Documentation generation completed successfully!")

    except Exception as e:
        logger.error(f"Generation failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
