"""
Chain Planner - Multi-step execution planning with fallback paths.
Version: 1.0

Single responsibility: Create execution plans with multiple paths
and fallback strategies for complex queries.
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
    EXTRACT_DATA = "extract_data"


@dataclass
class PlanStep:
    """Single step in execution plan."""
    step_number: int
    step_type: StepType
    tool_name: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    question: Optional[str] = None
    reason: str = ""
    depends_on: List[int] = field(default_factory=list)  # Step numbers this depends on
    output_key: Optional[str] = None  # Key to store result for later steps
    extract_fields: List[str] = field(default_factory=list)  # Fields to extract from response


@dataclass
class FallbackPath:
    """Alternative path when primary fails."""
    trigger_error: str  # Error type that triggers this path
    steps: List[PlanStep] = field(default_factory=list)
    reason: str = ""


@dataclass
class ExecutionPlan:
    """Complete execution plan with primary and fallback paths."""
    understanding: str
    is_simple: bool
    has_all_data: bool
    missing_data: List[str] = field(default_factory=list)
    primary_path: List[PlanStep] = field(default_factory=list)
    fallback_paths: Dict[int, List[FallbackPath]] = field(default_factory=dict)  # step_number -> fallbacks
    direct_response: Optional[str] = None
    extraction_hint: Optional[str] = None  # Hint for response extraction


class ChainPlanner:
    """
    Creates multi-step execution plans with fallback paths.

    Key features:
    1. Multi-step planning (not just single tool)
    2. Fallback paths for each step
    3. Dependency tracking between steps
    4. LLM-based intelligent planning
    """

    def __init__(self):
        """Initialize with OpenAI client."""
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
            ExecutionPlan with primary path and fallbacks
        """
        logger.info(f"Planning chain for: {query[:50]}...")

        # Check for simple cases first
        simple_plan = self._check_simple_cases(query, user_context, tool_scores)
        if simple_plan:
            return simple_plan

        # Build context for planner
        context_summary = self._summarize_context(user_context)
        tools_summary = self._summarize_tools(tool_scores[:10])  # More tools for chain

        # Ask LLM to plan
        plan_response = await self._get_plan_from_llm(
            query, context_summary, tools_summary
        )

        if not plan_response:
            return self._create_fallback_plan(query, tool_scores)

        return self._parse_plan_response(plan_response, tool_scores)

    def _check_simple_cases(
        self,
        query: str,
        user_context: Dict[str, Any],
        tool_scores: List[Dict[str, Any]]
    ) -> Optional[ExecutionPlan]:
        """Check for simple cases that don't need LLM planning."""
        query_lower = query.lower()

        # Greetings
        greetings = ["bok", "cao", "pozdrav", "hej", "zdravo", "hello", "hi"]
        if any(query_lower.strip() == g for g in greetings):
            return ExecutionPlan(
                understanding="Korisnik pozdravlja",
                is_simple=True,
                has_all_data=True,
                direct_response="Pozdrav! Kako vam mogu pomoći?"
            )

        # Thanks
        if any(x in query_lower for x in ["hvala", "thanks", "zahvaljujem"]):
            return ExecutionPlan(
                understanding="Korisnik zahvaljuje",
                is_simple=True,
                has_all_data=True,
                direct_response="Nema na čemu! Slobodno pitajte ako trebate još nešto."
            )

        # Help
        if query_lower.strip() in ["pomoć", "help", "pomozi", "što možeš"]:
            return ExecutionPlan(
                understanding="Korisnik traži pomoć",
                is_simple=True,
                has_all_data=True,
                direct_response=(
                    "Mogu vam pomoći s:\n"
                    "• **Kilometraža** - provjera ili unos km\n"
                    "• **Rezervacije** - rezervacija vozila\n"
                    "• **Podaci o vozilu** - registracija, lizing\n"
                    "• **Prijava kvara** - kreiranje slučaja\n\n"
                    "Što vas zanima?"
                )
            )

        # Very high score single tool (>= 0.95)
        if tool_scores and tool_scores[0].get("score", 0) >= 0.95:
            best_tool = tool_scores[0]
            return ExecutionPlan(
                understanding=f"Direktan upit za {best_tool['name']}",
                is_simple=True,
                has_all_data=self._has_required_context(best_tool, user_context),
                primary_path=[
                    PlanStep(
                        step_number=1,
                        step_type=StepType.EXECUTE_TOOL,
                        tool_name=best_tool["name"],
                        reason="Visoka podudarnost (>95%)"
                    )
                ],
                extraction_hint=self._get_extraction_hint(query)
            )

        return None

    def _has_required_context(
        self,
        tool: Dict[str, Any],
        user_context: Dict[str, Any]
    ) -> bool:
        """Check if user context has required parameters."""
        schema = tool.get("schema", {})
        required = schema.get("parameters", {}).get("required", [])

        for param in required:
            param_lower = param.lower()
            if "vehicle" in param_lower:
                if not user_context.get("vehicle", {}).get("id"):
                    return False
            elif "person" in param_lower or "driver" in param_lower:
                if not user_context.get("person_id"):
                    return False

        return True

    def _get_extraction_hint(self, query: str) -> Optional[str]:
        """Get extraction hint based on query."""
        query_lower = query.lower()

        hints = {
            "kilometra": "Mileage,LastMileage,CurrentMileage",
            "registraci": "RegistrationExpirationDate,ExpirationDate",
            "istje": "RegistrationExpirationDate,ExpirationDate",
            "lizing": "LeasingProvider,LeasingCompany",
            "tablice": "LicencePlate,RegistrationNumber",
            "vozilo": "FullVehicleName,VehicleName,Name",
        }

        for keyword, hint in hints.items():
            if keyword in query_lower:
                return hint

        return None

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

            # Get required and optional params
            params = schema.get("parameters", {}).get("properties", {})
            required = schema.get("parameters", {}).get("required", [])

            req_params = [p for p in required if p in params]
            opt_params = [p for p in params.keys() if p not in required][:3]

            lines.append(
                f"- {name} (score: {score:.2f}): {desc}\n"
                f"  Potrebni: {', '.join(req_params) or 'nema'}\n"
                f"  Opcionalni: {', '.join(opt_params) or 'nema'}"
            )

        return "\n".join(lines)

    async def _get_plan_from_llm(
        self,
        query: str,
        context: str,
        tools: str
    ) -> Optional[Dict[str, Any]]:
        """Get execution plan from LLM."""
        system_prompt = """
        Ti si MobilityOne Chain Planner. Tvoj zadatak je napraviti MULTI-STEP plan izvršenja.

        KRITIČNA PRAVILA:
        1. Analiziraj što korisnik ZAPRAVO traži
        2. Razmisli koje sve korake treba napraviti
        3. Za svaki korak definiraj PRIMARNI alat i FALLBACK alate
        4. Koristi NAJVIŠE 5 koraka
        5. UVIJEK predloži extraction_hint - koja polja izvući iz odgovora

        TIPOVI KORAKA:
        - execute_tool: Pozovi API alat
        - ask_user: Pitaj korisnika za podatak
        - user_select: Korisnik bira iz liste
        - confirm: Potvrda prije mutacije

        FALLBACK STRATEGIJE:
        - Ako primary tool vrati 403: Koristi alternativni tool
        - Ako primary tool vrati 404: Pitaj korisnika za pojašnjenje
        - Ako nedostaje parametar: Pitaj korisnika ILI koristi drugi tool za dohvat

        ODGOVORI U JSON FORMATU:
        {
        "understanding": "Što korisnik želi",
        "is_simple": true/false,
        "has_all_data": true/false,
        "missing_data": ["param1", "param2"],
        "extraction_hint": "Mileage,RegistrationExpirationDate",
        "primary_path": [
            {
            "step": 1,
            "type": "execute_tool",
            "tool": "get_MasterData",
            "reason": "Dohvati sve podatke vozila",
            "depends_on": [],
            "output_key": "vehicle_data",
            "extract_fields": ["Mileage", "RegistrationExpirationDate"]
            }
        ],
        "fallback_paths": {
            "1": [
            {
                "trigger_error": "403",
                "steps": [
                {"step": 1, "type": "execute_tool", "tool": "get_Vehicles", "reason": "Alternativa bez PersonId filtera"}
                ],
                "reason": "Ako get_MasterData vrati 403, probaj get_Vehicles"
            }
            ]
        },
        "direct_response": null
        }

        PRIMJER CHAIN PLAN-a:

        Upit: "Trebam rezervirati vozilo za sutra"

        {
        "understanding": "Korisnik želi rezervirati vozilo za sutra",
        "is_simple": false,
        "has_all_data": false,
        "missing_data": ["FromTime", "ToTime"],
        "extraction_hint": null,
        "primary_path": [
            {
            "step": 1,
            "type": "ask_user",
            "question": "Od kada vam treba vozilo? (npr. sutra u 9:00)",
            "reason": "Trebamo točno vrijeme početka"
            },
            {
            "step": 2,
            "type": "ask_user",
            "question": "Do kada? (npr. sutra u 17:00)",
            "reason": "Trebamo vrijeme završetka",
            "depends_on": [1]
            },
            {
            "step": 3,
            "type": "execute_tool",
            "tool": "get_AvailableVehicles",
            "reason": "Pronađi slobodna vozila u tom periodu",
            "depends_on": [1, 2],
            "output_key": "available_vehicles"
            },
            {
            "step": 4,
            "type": "user_select",
            "question": "Koje vozilo želite?",
            "reason": "Korisnik bira iz dostupnih",
            "depends_on": [3]
            },
            {
            "step": 5,
            "type": "confirm",
            "question": "Potvrdite rezervaciju?",
            "reason": "Potvrda prije kreiranja",
            "depends_on": [4]
            },
            {
            "step": 6,
            "type": "execute_tool",
            "tool": "post_VehicleCalendar",
            "reason": "Kreiraj rezervaciju",
            "depends_on": [5]
            }
        ],
        "fallback_paths": {
            "3": [
            {
                "trigger_error": "no_results",
                "steps": [
                {"step": 1, "type": "ask_user", "question": "Nema slobodnih vozila u tom periodu. Želite li drugi termin?"}
                ],
                "reason": "Ako nema vozila, pitaj za drugi termin"
            }
            ]
        }
        }"""

        user_message = f"""UPIT: {query}

KONTEKST:
{context}

DOSTUPNI ALATI:
{tools}

Napravi CHAIN PLAN izvršenja u JSON formatu."""

        try:
            response = await self.openai.chat.completions.create(
                model=settings.AZURE_OPENAI_DEPLOYMENT_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                temperature=0.1,
                max_tokens=1500,
                response_format={"type": "json_object"}
            )

            content = response.choices[0].message.content
            return json.loads(content)

        except Exception as e:
            logger.error(f"ChainPlanner LLM error: {e}")
            return None

    def _parse_plan_response(
        self,
        response: Dict[str, Any],
        tool_scores: List[Dict[str, Any]]
    ) -> ExecutionPlan:
        """Parse LLM response into ExecutionPlan."""
        primary_steps = []

        for step_data in response.get("primary_path", []):
            step_type_str = step_data.get("type", "execute_tool")
            try:
                step_type = StepType(step_type_str)
            except ValueError:
                step_type = StepType.EXECUTE_TOOL

            step = PlanStep(
                step_number=step_data.get("step", len(primary_steps) + 1),
                step_type=step_type,
                tool_name=step_data.get("tool"),
                parameters=step_data.get("params", {}),
                question=step_data.get("question"),
                reason=step_data.get("reason", ""),
                depends_on=step_data.get("depends_on", []),
                output_key=step_data.get("output_key"),
                extract_fields=step_data.get("extract_fields", [])
            )
            primary_steps.append(step)

        # Parse fallback paths
        fallback_paths = {}
        for step_num_str, fallbacks_data in response.get("fallback_paths", {}).items():
            try:
                step_num = int(step_num_str)
            except ValueError:
                continue

            fallbacks = []
            for fb_data in fallbacks_data:
                fb_steps = []
                for fb_step_data in fb_data.get("steps", []):
                    fb_step = PlanStep(
                        step_number=fb_step_data.get("step", 1),
                        step_type=StepType(fb_step_data.get("type", "execute_tool")),
                        tool_name=fb_step_data.get("tool"),
                        question=fb_step_data.get("question"),
                        reason=fb_step_data.get("reason", "")
                    )
                    fb_steps.append(fb_step)

                fallback = FallbackPath(
                    trigger_error=fb_data.get("trigger_error", ""),
                    steps=fb_steps,
                    reason=fb_data.get("reason", "")
                )
                fallbacks.append(fallback)

            fallback_paths[step_num] = fallbacks

        return ExecutionPlan(
            understanding=response.get("understanding", ""),
            is_simple=response.get("is_simple", True),
            has_all_data=response.get("has_all_data", False),
            missing_data=response.get("missing_data", []),
            primary_path=primary_steps,
            fallback_paths=fallback_paths,
            direct_response=response.get("direct_response"),
            extraction_hint=response.get("extraction_hint")
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
                direct_response="Nisam siguran kako mogu pomoći. Možete li pojasniti?"
            )

        # Use top 2 tools as primary + fallback
        best_tool = tool_scores[0]
        alt_tool = tool_scores[1] if len(tool_scores) > 1 else None

        primary_path = [
            PlanStep(
                step_number=1,
                step_type=StepType.EXECUTE_TOOL,
                tool_name=best_tool["name"],
                reason="Najbolji pronađeni alat"
            )
        ]

        fallback_paths = {}
        if alt_tool:
            fallback_paths[1] = [
                FallbackPath(
                    trigger_error="any",
                    steps=[
                        PlanStep(
                            step_number=1,
                            step_type=StepType.EXECUTE_TOOL,
                            tool_name=alt_tool["name"],
                            reason="Alternativni alat"
                        )
                    ],
                    reason="Ako primarni alat ne radi"
                )
            ]

        return ExecutionPlan(
            understanding=f"Korištenje najboljeg alata: {best_tool['name']}",
            is_simple=True,
            has_all_data=False,
            primary_path=primary_path,
            fallback_paths=fallback_paths,
            extraction_hint=self._get_extraction_hint(query)
        )


# Singleton instance
_chain_planner = None


def get_chain_planner() -> ChainPlanner:
    """Get singleton instance."""
    global _chain_planner
    if _chain_planner is None:
        _chain_planner = ChainPlanner()
    return _chain_planner
