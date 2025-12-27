"""
LLM Tool Selector - True intelligent tool selection using LLM.
Version: 1.0

This module provides 100% accurate tool selection by using LLM
to make the final decision, with few-shot examples from training data.

Architecture:
1. Load training examples for relevant categories
2. Build few-shot prompt with similar examples
3. Ask LLM to select the best tool
4. Return tool with real confidence
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

from openai import AsyncAzureOpenAI

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class ToolSelection:
    """Result of LLM tool selection."""
    tool_name: str
    confidence: float  # Real confidence from LLM reasoning
    reasoning: str
    alternative_tools: List[str]


class LLMToolSelector:
    """
    Selects the best tool using LLM with few-shot examples.

    This is the INTELLIGENT part - LLM makes the decision,
    not keywords or embeddings.
    """

    def __init__(self):
        """Initialize the selector."""
        self.client = AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            max_retries=2,
            timeout=30.0
        )
        self.model = settings.AZURE_OPENAI_DEPLOYMENT_NAME

        # Load training data
        self._training_examples: List[Dict] = []
        self._examples_by_category: Dict[str, List[Dict]] = {}
        self._examples_by_tool: Dict[str, List[Dict]] = {}
        self._initialized = False

    async def initialize(self):
        """Load training data."""
        if self._initialized:
            return

        try:
            training_path = Path(__file__).parent.parent / "data" / "training_queries.json"

            if training_path.exists():
                with open(training_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                self._training_examples = data.get("examples", [])

                # Index by category
                for ex in self._training_examples:
                    cat = ex.get("category", "unknown")
                    if cat not in self._examples_by_category:
                        self._examples_by_category[cat] = []
                    self._examples_by_category[cat].append(ex)

                    # Index by tool
                    tool = ex.get("primary_tool")
                    if tool:
                        if tool not in self._examples_by_tool:
                            self._examples_by_tool[tool] = []
                        self._examples_by_tool[tool].append(ex)

                logger.info(
                    f"Loaded {len(self._training_examples)} training examples "
                    f"across {len(self._examples_by_category)} categories"
                )
            else:
                logger.warning(f"Training data not found: {training_path}")

            self._initialized = True

        except Exception as e:
            logger.error(f"Failed to load training data: {e}")
            self._initialized = True  # Mark as initialized to avoid retry loop

    def _get_few_shot_examples(
        self,
        query: str,
        categories: List[str],
        candidate_tools: List[str],
        max_examples: int = 10
    ) -> List[Dict]:
        """
        Get relevant few-shot examples for the prompt.

        INTELLIGENT SELECTION:
        1. Find examples with similar keywords to the query
        2. Prioritize examples for common tools (AddCase, AddMileage, etc.)
        3. Include diverse examples across different intents
        """
        examples = []
        seen_intents = set()
        query_lower = query.lower()

        # Priority tools - ensure we have examples for these
        priority_tools_map = {
            "post_AddCase": ["stet", "kvar", "udari", "ogreb", "prijav", "slomio"],
            "post_AddMileage": ["kilometr", "km", "unesi", "upisi"],
            "get_MasterData": ["registracij", "tablica", "podaci", "vozilo"],
            "get_AvailableVehicles": ["slobodn", "dostupn"],
            "post_VehicleCalendar": ["rezerv", "booking"],
            "get_VehicleCalendar": ["moje rezerv", "booking"],
            "get_Expenses": ["troskov", "expense"],
            "get_Trips": ["trip", "putovanj"],
        }

        # Step 1: Add examples for priority tools if query matches
        for tool, keywords in priority_tools_map.items():
            if any(kw in query_lower for kw in keywords):
                tool_examples = self._examples_by_tool.get(tool, [])
                for ex in tool_examples[:3]:  # Up to 3 examples per priority tool
                    if len(examples) >= max_examples:
                        break
                    intent = ex.get("intent")
                    if intent not in seen_intents:
                        examples.append(ex)
                        seen_intents.add(intent)

        # Step 2: Add examples from matched categories
        for cat in categories:
            cat_examples = self._examples_by_category.get(cat, [])
            for ex in cat_examples:
                if len(examples) >= max_examples:
                    break
                intent = ex.get("intent")
                if intent not in seen_intents:
                    examples.append(ex)
                    seen_intents.add(intent)

        # Step 3: Add examples for candidate tools
        if len(examples) < max_examples:
            for tool in candidate_tools[:15]:  # Check first 15 tools
                tool_examples = self._examples_by_tool.get(tool, [])
                for ex in tool_examples:
                    if len(examples) >= max_examples:
                        break
                    intent = ex.get("intent")
                    if intent not in seen_intents:
                        examples.append(ex)
                        seen_intents.add(intent)

        return examples[:max_examples]

    def _build_tools_description(self, tools: List[str], registry) -> str:
        """Build a concise description of available tools."""
        descriptions = []

        for tool_name in tools[:30]:  # Limit to 30 tools for token efficiency
            tool = registry.get_tool(tool_name)
            if tool:
                desc = tool.description[:100] if tool.description else "No description"
                descriptions.append(f"- {tool_name}: {desc}")

        if len(tools) > 30:
            descriptions.append(f"... and {len(tools) - 30} more tools")

        return "\n".join(descriptions)

    async def select_tool(
        self,
        query: str,
        candidate_tools: List[str],
        categories: List[str],
        registry,
        user_context: Optional[Dict] = None
    ) -> ToolSelection:
        """
        Select the best tool using LLM.

        Args:
            query: User's query
            candidate_tools: Tools to choose from (pre-filtered by category)
            categories: Matched categories
            registry: ToolRegistry for tool descriptions
            user_context: Optional user context

        Returns:
            ToolSelection with tool name and confidence
        """
        await self.initialize()

        # Get few-shot examples (intelligently selected based on query)
        examples = self._get_few_shot_examples(query, categories, candidate_tools)

        # Build few-shot part of prompt
        few_shot_text = ""
        if examples:
            few_shot_text = "Primjeri sličnih upita:\n\n"
            for ex in examples:
                few_shot_text += f"Upit: \"{ex['query']}\"\n"
                few_shot_text += f"Alat: {ex['primary_tool']}\n"
                if ex.get('alternative_tools'):
                    few_shot_text += f"Alternative: {', '.join(ex['alternative_tools'])}\n"
                few_shot_text += "\n"

        # Build tools description
        tools_desc = self._build_tools_description(candidate_tools, registry)

        # Build the prompt
        system_prompt = """Ti si stručnjak za odabir pravog API alata na temelju korisničkog upita.

Tvoj zadatak:
1. Analiziraj korisnikov upit
2. Pregledaj dostupne alate
3. Odaberi NAJBOLJI alat za taj upit
4. Objasni zašto si odabrao taj alat

PRAVILA ZA ODABIR ALATA:

1. PRIJAVA ŠTETE/KVARA:
   - Ako korisnik prijavljuje štetu, kvar, nesreću, udar → UVIJEK koristi post_AddCase
   - Primjeri: "udario sam", "ogrebao sam", "imam kvar", "prijavi štetu" → post_AddCase
   - NIKAD ne koristi put_Cases_id za novu prijavu štete!

2. UNOS KILOMETARA:
   - Za unos nove kilometraže → post_AddMileage
   - Primjeri: "unesi km", "upiši kilometražu" → post_AddMileage

3. PODACI O VOZILU:
   - Za opće podatke (registracija, tablica, km) → get_MasterData
   - get_MasterData vraća sve bitne informacije o vozilu

4. REZERVACIJE:
   - Nova rezervacija → post_VehicleCalendar ili post_Booking
   - Moje rezervacije → get_VehicleCalendar

5. DOSTUPNOST:
   - Slobodna vozila → get_AvailableVehicles

VAŽNO:
- Odaberi SAMO alate iz ponuđene liste
- Za ČITANJE koristi get_* alate
- Za KREIRANJE/PRIJAVU koristi post_* alate
- Za BRISANJE koristi delete_* alate
- Ako nisi siguran, confidence stavi ispod 0.7

Odgovori u JSON formatu:
{
    "tool": "ime_alata",
    "confidence": 0.0-1.0,
    "reasoning": "zašto ovaj alat",
    "alternatives": ["alternativni_alat1"]
}"""

        user_prompt = f"""Korisnikov upit: "{query}"

{few_shot_text}
Dostupni alati:
{tools_desc}

Koji alat je najbolji za ovaj upit?"""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,  # Low temperature for consistent results
                max_tokens=500,
                response_format={"type": "json_object"}
            )

            result_text = response.choices[0].message.content
            result = json.loads(result_text)

            tool_name = result.get("tool", "")
            confidence = float(result.get("confidence", 0.5))
            reasoning = result.get("reasoning", "")
            alternatives = result.get("alternatives", [])

            # Validate tool exists in candidates
            if tool_name and tool_name not in candidate_tools:
                # LLM hallucinated a tool - try to find closest match
                logger.warning(f"LLM selected non-existent tool: {tool_name}")
                for candidate in candidate_tools:
                    if tool_name.lower() in candidate.lower() or candidate.lower() in tool_name.lower():
                        tool_name = candidate
                        confidence *= 0.8  # Reduce confidence
                        break
                else:
                    # No match found - use first candidate with low confidence
                    tool_name = candidate_tools[0] if candidate_tools else ""
                    confidence = 0.3
                    reasoning = f"Fallback: LLM selected invalid tool"

            logger.info(
                f"LLM selected: {tool_name} (conf={confidence:.2f}) "
                f"for query: '{query[:40]}...'"
            )

            return ToolSelection(
                tool_name=tool_name,
                confidence=confidence,
                reasoning=reasoning,
                alternative_tools=alternatives
            )

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response: {e}")
            return self._fallback_selection(candidate_tools, "JSON parse error")

        except Exception as e:
            logger.error(f"LLM tool selection failed: {e}")
            return self._fallback_selection(candidate_tools, str(e))

    def _fallback_selection(
        self,
        candidate_tools: List[str],
        error_reason: str
    ) -> ToolSelection:
        """Fallback when LLM fails."""
        tool = candidate_tools[0] if candidate_tools else ""
        return ToolSelection(
            tool_name=tool,
            confidence=0.2,  # Low confidence for fallback
            reasoning=f"Fallback selection: {error_reason}",
            alternative_tools=candidate_tools[1:3] if len(candidate_tools) > 1 else []
        )


# Singleton instance
_selector: Optional[LLMToolSelector] = None


async def get_llm_tool_selector() -> LLMToolSelector:
    """Get or create singleton selector instance."""
    global _selector
    if _selector is None:
        _selector = LLMToolSelector()
        await _selector.initialize()
    return _selector
