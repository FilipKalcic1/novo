"""
Planner - Chain of Thought execution planning.
Version: 1.0

Single responsibility: Analyze user intent and create execution plan.
"""

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, List, Optional

from openai import AsyncAzureOpenAI

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class StepType(Enum):
    """Type of execution step."""
    EXECUTE_TOOL = "execute_tool"
    ASK_USER = "ask_user"
    USER_SELECT = "user_select"
    CONFIRM = "confirm"


@dataclass
class PlanStep:
    """Single step in execution plan."""
    step_number: int
    step_type: StepType
    tool_name: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    question: Optional[str] = None
    reason: str = ""


@dataclass
class ExecutionPlan:
    """Execution plan for user request."""
    understanding: str
    is_simple: bool
    has_all_data: bool
    missing_data: List[str] = field(default_factory=list)
    steps: List[PlanStep] = field(default_factory=list)
    direct_response: Optional[str] = None


class Planner:
    """
    Creates execution plans using Chain of Thought reasoning.

    Responsibilities:
    - Analyze user intent
    - Determine if query is simple or complex
    - Create step-by-step execution plan
    - Identify missing data
    """

    def __init__(self):
        """Initialize planner with OpenAI client."""
        self.openai = AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION
        )

    async def create_plan(
        self,
        query: str,
        user_context: Dict[str, Any],
        available_tools: List[Dict[str, Any]],
        tool_scores: List[Dict[str, Any]]
    ) -> ExecutionPlan:
        """
        Create execution plan for user query.

        Args:
            query: User's natural language query
            user_context: User data (person_id, vehicle, etc.)
            available_tools: List of tool schemas
            tool_scores: Tools with relevance scores

        Returns:
            ExecutionPlan with steps to execute
        """
        logger.info(f"Planning for: {query[:50]}...")

        # Build context for planner
        context_summary = self._summarize_context(user_context)
        tools_summary = self._summarize_tools(tool_scores[:5])

        # Ask LLM to plan
        plan_response = await self._get_plan_from_llm(
            query, context_summary, tools_summary
        )

        if not plan_response:
            return self._create_fallback_plan(query, tool_scores)

        return self._parse_plan_response(plan_response, tool_scores)

    def _summarize_context(self, user_context: Dict[str, Any]) -> str:
        """Summarize user context for planner."""
        parts = []

        if user_context.get("person_id"):
            parts.append(f"person_id: {user_context['person_id']}")

        if user_context.get("display_name"):
            parts.append(f"ime: {user_context['display_name']}")

        vehicle = user_context.get("vehicle", {})
        if vehicle:
            if vehicle.get("id"):
                parts.append(f"vehicle_id: {vehicle['id']}")
            if vehicle.get("plate"):
                parts.append(f"tablica: {vehicle['plate']}")
            if vehicle.get("name"):
                parts.append(f"vozilo: {vehicle['name']}")

        if not parts:
            return "Nema dodatnih podataka o korisniku."

        return "Poznati podaci: " + ", ".join(parts)

    def _summarize_tools(self, tools: List[Dict[str, Any]]) -> str:
        """Summarize available tools for planner."""
        lines = []
        for t in tools:
            name = t.get("name", "")
            score = t.get("score", 0)
            schema = t.get("schema", {})
            desc = schema.get("description", "")[:100]

            # Get required params
            params = schema.get("parameters", {}).get("properties", {})
            required = schema.get("parameters", {}).get("required", [])
            req_params = [p for p in required if p in params]

            lines.append(
                f"- {name} (score: {score:.2f}): {desc}"
                f"\n  Potrebni parametri: {', '.join(req_params) or 'nema'}"
            )

        return "\n".join(lines)

    async def _get_plan_from_llm(
        self,
        query: str,
        context: str,
        tools: str
    ) -> Optional[Dict[str, Any]]:
        """Get execution plan from LLM."""
        system_prompt = """Ti si MobilityOne Planner. Tvoj zadatak je analizirati korisnikov upit i napraviti plan izvršenja.

PRAVILA:
1. Analiziraj što korisnik ZAPRAVO traži
2. Provjeri koje podatke imaš (kontekst)
3. Provjeri koje alate možeš koristiti i što im treba
4. Napravi plan koraka (max 3 koraka)

KRITIČNO - ODABIR ALATA PO SCORE-u:
- Svaki alat ima SCORE (0.00 do 1.00) koji pokazuje kvalitetu
- Score se računa iz: uspješnosti, permisija, brzine
- UVIJEK KORISTI ALAT S NAJVIŠIM SCORE-om kao prvi izbor!
- NIKADA ne koristi alat sa score < 0.30 - takvi alati ne rade ili nemaju permisije
- Ako najbolji alat ima score > 0.80, koristi njega i ne razmišljaj o drugim alatima

TIPOVI KORAKA:
- execute_tool: Pozovi API alat
- ask_user: Pitaj korisnika za podatak
- user_select: Korisnik bira iz liste
- confirm: Potvrda prije mutacije (POST/PUT/DELETE)

ODGOVORI U JSON FORMATU:
{
  "understanding": "Što korisnik želi (1 rečenica)",
  "is_simple": true/false,
  "has_all_data": true/false,
  "missing_data": ["parametar1", "parametar2"],
  "steps": [
    {"step": 1, "type": "execute_tool", "tool": "ime_alata", "reason": "zašto"},
    {"step": 2, "type": "ask_user", "param": "from", "question": "Od kada?", "reason": "treba period"}
  ],
  "direct_response": null ili "Odgovor ako ne treba alat"
}

PRIMJERI:

Upit: "Kolika mi je kilometraža?"
Kontekst: vehicle_id: abc-123
Alati:
  - get_MasterData (score: 1.00): Dohvaća sve podatke vozila uključujući kilometražu
  - get_LatestMileageReports (score: 0.15): FORBIDDEN - nema permisije
{
  "understanding": "Korisnik želi znati kilometražu svog vozila",
  "is_simple": true,
  "has_all_data": true,
  "missing_data": [],
  "steps": [
    {"step": 1, "type": "execute_tool", "tool": "get_MasterData", "reason": "najbolji score (1.00) i ima sve potrebne podatke"}
  ],
  "direct_response": null
}

Upit: "Trebam vozilo za sutra"
Kontekst: person_id: f18e-..., nema vehicle_id
{
  "understanding": "Korisnik želi rezervirati vozilo za sutra",
  "is_simple": false,
  "has_all_data": false,
  "missing_data": ["from", "to"],
  "steps": [
    {"step": 1, "type": "ask_user", "param": "from", "question": "Od kada vam treba vozilo? (npr. sutra u 9:00)", "reason": "treba početno vrijeme"},
    {"step": 2, "type": "ask_user", "param": "to", "question": "Do kada?", "reason": "treba završno vrijeme"},
    {"step": 3, "type": "execute_tool", "tool": "get_AvailableVehicles", "reason": "pronađi slobodna vozila"},
    {"step": 4, "type": "user_select", "reason": "korisnik bira vozilo"},
    {"step": 5, "type": "confirm", "reason": "potvrda rezervacije"},
    {"step": 6, "type": "execute_tool", "tool": "post_VehicleCalendar", "reason": "kreiraj rezervaciju"}
  ],
  "direct_response": null
}

Upit: "Bok"
{
  "understanding": "Korisnik pozdravlja",
  "is_simple": true,
  "has_all_data": true,
  "missing_data": [],
  "steps": [],
  "direct_response": "Pozdrav! Kako vam mogu pomoći?"
}"""

        user_message = f"""UPIT: {query}

KONTEKST:
{context}

DOSTUPNI ALATI:
{tools}

Napravi plan izvršenja u JSON formatu."""

        try:
            response = await self.openai.chat.completions.create(
                model=settings.AZURE_OPENAI_DEPLOYMENT_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                temperature=0.1,
                max_tokens=800,
                response_format={"type": "json_object"}
            )

            content = response.choices[0].message.content
            return json.loads(content)

        except Exception as e:
            logger.error(f"Planner LLM error: {e}")
            return None

    def _parse_plan_response(
        self,
        response: Dict[str, Any],
        tool_scores: List[Dict[str, Any]]
    ) -> ExecutionPlan:
        """Parse LLM response into ExecutionPlan."""
        steps = []

        for step_data in response.get("steps", []):
            step_type_str = step_data.get("type", "execute_tool")
            try:
                step_type = StepType(step_type_str)
            except ValueError:
                step_type = StepType.EXECUTE_TOOL

            step = PlanStep(
                step_number=step_data.get("step", len(steps) + 1),
                step_type=step_type,
                tool_name=step_data.get("tool"),
                parameters=step_data.get("params", {}),
                question=step_data.get("question"),
                reason=step_data.get("reason", "")
            )
            steps.append(step)

        return ExecutionPlan(
            understanding=response.get("understanding", ""),
            is_simple=response.get("is_simple", True),
            has_all_data=response.get("has_all_data", False),
            missing_data=response.get("missing_data", []),
            steps=steps,
            direct_response=response.get("direct_response")
        )

    def _create_fallback_plan(
        self,
        query: str,
        tool_scores: List[Dict[str, Any]]
    ) -> ExecutionPlan:
        """Create fallback plan when LLM fails."""
        if not tool_scores:
            return ExecutionPlan(
                understanding="Nije pronađen odgovarajući alat",
                is_simple=True,
                has_all_data=True,
                steps=[],
                direct_response="Nisam siguran kako mogu pomoći. Možete li pojasniti?"
            )

        best_tool = tool_scores[0]
        return ExecutionPlan(
            understanding=f"Korištenje najboljeg alata: {best_tool['name']}",
            is_simple=True,
            has_all_data=False,
            steps=[
                PlanStep(
                    step_number=1,
                    step_type=StepType.EXECUTE_TOOL,
                    tool_name=best_tool["name"],
                    reason="Fallback na najbolji pronađeni alat"
                )
            ]
        )
