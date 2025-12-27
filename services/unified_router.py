"""
Unified Router - Single LLM makes ALL routing decisions.
Version: 1.0

This replaces the complex multi-layer routing with a single, reliable LLM decision.

Architecture:
1. Gather context (current state, user info, tools)
2. Single LLM call decides everything
3. Execute based on decision

The LLM receives:
- User query
- Current conversation state (flow, missing params)
- User context (vehicle, person)
- Available primary tools (30 most common)
- Few-shot examples from training data

The LLM outputs:
- action: "continue_flow" | "exit_flow" | "start_flow" | "simple_api" | "direct_response"
- tool: tool name or null
- params: extracted parameters
- flow_type: booking | mileage | case | None
- response: direct response text (for direct_response action)
- reasoning: explanation
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from pathlib import Path

from openai import AsyncAzureOpenAI

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class RouterDecision:
    """Result of unified routing decision."""
    action: str  # continue_flow, exit_flow, start_flow, simple_api, direct_response
    tool: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    flow_type: Optional[str] = None  # booking, mileage, case
    response: Optional[str] = None  # For direct_response
    reasoning: str = ""
    confidence: float = 0.0


# Primary tools - the 30 most common operations
PRIMARY_TOOLS = {
    # Vehicle Info (READ)
    "get_MasterData": "Dohvati podatke o vozilu (registracija, kilometraža, servis)",
    "get_Vehicles_id": "Dohvati detalje specifičnog vozila",

    # Availability & Booking
    "get_AvailableVehicles": "Provjeri dostupna/slobodna vozila za period",
    "get_VehicleCalendar": "Dohvati moje rezervacije",
    "post_VehicleCalendar": "Kreiraj novu rezervaciju vozila",
    "delete_VehicleCalendar_id": "Obriši/otkaži rezervaciju",

    # Mileage
    "get_LatestMileageReports": "Dohvati zadnju kilometražu",
    "get_MileageReports": "Dohvati izvještaje o kilometraži",
    "post_AddMileage": "Unesi/upiši novu kilometražu",

    # Case/Damage
    "post_AddCase": "Prijavi štetu, kvar, problem, nesreću",
    "get_Cases": "Dohvati prijavljene slučajeve",

    # Expenses
    "get_Expenses": "Dohvati troškove",
    "get_ExpenseGroups": "Dohvati grupe troškova",

    # Trips
    "get_Trips": "Dohvati putovanja/tripove",

    # Dashboard
    "get_DashboardItems": "Dohvati dashboard podatke",
}

# Flow triggers - which tools trigger which flows
FLOW_TRIGGERS = {
    "post_VehicleCalendar": "booking",
    "get_AvailableVehicles": "booking",
    "post_AddMileage": "mileage",
    "post_AddCase": "case",
}

# Exit signals - phrases that indicate user wants to exit current flow
EXIT_SIGNALS = [
    "ne želim", "necu", "nećem", "nećeš", "odustani", "odustajem",
    "zapravo", "ipak", "ne treba", "nemoj", "stani", "stop",
    "nešto drugo", "drugo pitanje", "promijeni", "cancel",
    "hoću nešto drugo", "želim nešto drugo"
]


class UnifiedRouter:
    """
    Single LLM router that makes all routing decisions.

    This is the ONLY decision point - no keyword matching, no filtering.
    The LLM sees everything and decides.
    """

    def __init__(self):
        """Initialize router."""
        self.client = AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            max_retries=2,
            timeout=30.0
        )
        self.model = settings.AZURE_OPENAI_DEPLOYMENT_NAME

        # Training examples
        self._training_examples: List[Dict] = []
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
                logger.info(f"UnifiedRouter: Loaded {len(self._training_examples)} training examples")
        except Exception as e:
            logger.error(f"Failed to load training data: {e}")

        self._initialized = True

    def _check_exit_signal(self, query: str) -> bool:
        """Check if query contains exit/cancellation signal."""
        query_lower = query.lower()
        return any(signal in query_lower for signal in EXIT_SIGNALS)

    def _check_greeting(self, query: str) -> Optional[str]:
        """Check if query is a greeting and return response."""
        query_lower = query.lower().strip()

        greetings = {
            "bok": "Bok! Kako vam mogu pomoći?",
            "hej": "Hej! Kako vam mogu pomoći?",
            "pozdrav": "Pozdrav! Kako vam mogu pomoći?",
            "zdravo": "Zdravo! Kako vam mogu pomoći?",
            "dobar dan": "Dobar dan! Kako vam mogu pomoći?",
            "dobro jutro": "Dobro jutro! Kako vam mogu pomoći?",
            "dobra večer": "Dobra večer! Kako vam mogu pomoći?",
            "hvala": "Nema na čemu! Trebate li još nešto?",
            "thanks": "You're welcome! Need anything else?",
            "help": "Mogu vam pomoći s:\n• Rezervacija vozila\n• Unos kilometraže\n• Prijava kvara\n• Informacije o vozilu",
            "pomoc": "Mogu vam pomoći s:\n• Rezervacija vozila\n• Unos kilometraže\n• Prijava kvara\n• Informacije o vozilu",
            "pomoć": "Mogu vam pomoći s:\n• Rezervacija vozila\n• Unos kilometraže\n• Prijava kvara\n• Informacije o vozilu",
        }

        for greeting, response in greetings.items():
            if query_lower == greeting or query_lower.startswith(greeting + " "):
                return response

        return None

    def _get_few_shot_examples(self, query: str, current_flow: Optional[str] = None) -> str:
        """Get relevant few-shot examples."""
        examples = []
        query_lower = query.lower()

        # Keywords to match examples
        keywords_map = {
            "kilometr": ["post_AddMileage", "get_MasterData", "get_MileageReports"],
            "km": ["post_AddMileage", "get_MasterData"],
            "registracij": ["get_MasterData"],
            "tablica": ["get_MasterData"],
            "rezerv": ["post_VehicleCalendar", "get_VehicleCalendar", "get_AvailableVehicles"],
            "booking": ["post_VehicleCalendar", "get_VehicleCalendar"],
            "slobodn": ["get_AvailableVehicles"],
            "dostupn": ["get_AvailableVehicles"],
            "šteta": ["post_AddCase"],
            "kvar": ["post_AddCase"],
            "prijavi": ["post_AddCase"],
            "troskov": ["get_Expenses"],
            "trip": ["get_Trips"],
            "putovanj": ["get_Trips"],
        }

        # Find matching tools
        matching_tools = set()
        for keyword, tools in keywords_map.items():
            if keyword in query_lower:
                matching_tools.update(tools)

        # Get examples for matching tools
        for ex in self._training_examples:
            if ex.get("primary_tool") in matching_tools:
                examples.append(ex)
                if len(examples) >= 5:
                    break

        if not examples:
            return ""

        result = "Primjeri sličnih upita:\n"
        for ex in examples[:5]:
            result += f'- "{ex["query"]}" → {ex["primary_tool"]}\n'

        return result

    async def route(
        self,
        query: str,
        user_context: Dict[str, Any],
        conversation_state: Optional[Dict] = None
    ) -> RouterDecision:
        """
        Make routing decision using LLM.

        Args:
            query: User's message
            user_context: User info (vehicle, person_id, etc.)
            conversation_state: Current flow state if any

        Returns:
            RouterDecision with action, tool, params, etc.
        """
        await self.initialize()

        # Quick checks before LLM

        # 1. Check for greeting
        greeting_response = self._check_greeting(query)
        if greeting_response:
            return RouterDecision(
                action="direct_response",
                response=greeting_response,
                reasoning="Greeting detected",
                confidence=1.0
            )

        # 2. Check for exit signal when in flow
        in_flow = conversation_state and conversation_state.get("flow")
        if in_flow and self._check_exit_signal(query):
            return RouterDecision(
                action="exit_flow",
                reasoning="Exit signal detected",
                confidence=1.0
            )

        # 3. Build LLM prompt
        return await self._llm_route(query, user_context, conversation_state)

    async def _llm_route(
        self,
        query: str,
        user_context: Dict[str, Any],
        conversation_state: Optional[Dict]
    ) -> RouterDecision:
        """Make routing decision using LLM."""

        # Build context description
        vehicle = user_context.get("vehicle", {})
        vehicle_info = ""
        if vehicle.get("id"):
            vehicle_info = f"Korisnikovo vozilo: {vehicle.get('name', 'N/A')} ({vehicle.get('plate', 'N/A')})"
        else:
            vehicle_info = "Korisnik NEMA dodijeljeno vozilo"

        # Build flow state description
        flow_info = "Korisnik je u IDLE stanju (novi upit)"
        if conversation_state:
            flow = conversation_state.get("flow")
            state = conversation_state.get("state")
            missing = conversation_state.get("missing_params", [])
            tool = conversation_state.get("tool")

            if flow:
                flow_info = (
                    f"Korisnik je U TIJEKU flow-a:\n"
                    f"  - Flow: {flow}\n"
                    f"  - State: {state}\n"
                    f"  - Tool: {tool}\n"
                    f"  - Nedostaju parametri: {missing}"
                )

        # Build tools description
        tools_desc = "Dostupni alati:\n"
        for tool_name, description in PRIMARY_TOOLS.items():
            tools_desc += f"  - {tool_name}: {description}\n"

        # Get few-shot examples
        examples = self._get_few_shot_examples(query, conversation_state.get("flow") if conversation_state else None)

        # Build system prompt
        system_prompt = f"""Ti si routing sustav za MobilityOne fleet management bot.

TVOJ ZADATAK: Odluči što napraviti s korisnikovim upitom.

{vehicle_info}

{flow_info}

{tools_desc}

{examples}

PRAVILA:

1. AKO je korisnik U TIJEKU flow-a:
   - Ako korisnik daje tražene parametre → action="continue_flow"
   - Ako korisnik želi NEŠTO DRUGO (ne vezano uz flow) → action="exit_flow"
   - PREPOZNAJ: "ne želim", "odustani", "nešto drugo", "zapravo hoću..." = exit_flow

2. AKO korisnik NIJE u flow-u:
   - Ako treba pokrenuti flow (rezervacija, unos km, prijava štete) → action="start_flow"
   - Ako je jednostavan upit (dohvat podataka) → action="simple_api"
   - Ako je pozdrav ili zahvala → action="direct_response"

3. ODABIR ALATA:
   - "unesi km", "upiši kilometražu", "mogu li upisati" → post_AddMileage (WRITE!)
   - "koliko imam km", "moja kilometraža" → get_MasterData (READ)
   - "registracija", "tablica", "podaci o vozilu" → get_MasterData
   - "slobodna vozila", "dostupna vozila" → get_AvailableVehicles
   - "trebam auto", "rezerviraj" → get_AvailableVehicles (pa flow)
   - "moje rezervacije" → get_VehicleCalendar
   - "prijavi štetu", "kvar", "udario sam" → post_AddCase
   - "troškovi" → get_Expenses
   - "tripovi", "putovanja" → get_Trips

4. FLOW TYPES:
   - booking: za rezervacije
   - mileage: za unos kilometraže
   - case: za prijavu štete/kvara

ODGOVORI U JSON FORMATU:
{{
    "action": "continue_flow|exit_flow|start_flow|simple_api|direct_response",
    "tool": "ime_alata ili null",
    "params": {{}},
    "flow_type": "booking|mileage|case ili null",
    "response": "tekst odgovora za direct_response ili null",
    "reasoning": "kratko objašnjenje odluke",
    "confidence": 0.0-1.0
}}"""

        user_prompt = f'Korisnikov upit: "{query}"'

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,
                max_tokens=500,
                response_format={"type": "json_object"}
            )

            result_text = response.choices[0].message.content
            result = json.loads(result_text)

            logger.info(
                f"UNIFIED ROUTER: '{query[:30]}...' → "
                f"action={result.get('action')}, tool={result.get('tool')}, "
                f"flow={result.get('flow_type')}"
            )

            action = result.get("action", "simple_api")

            # CRITICAL FIX: Prevent exit_flow when not in a flow
            # This prevents infinite loop in engine when LLM hallucinates exit_flow
            if action == "exit_flow" and not conversation_state:
                logger.warning(
                    f"LLM returned exit_flow but no active flow - "
                    f"converting to simple_api. Query: '{query[:40]}...'"
                )
                action = "simple_api"
                # Try to use the tool from LLM response, or fallback to MasterData
                if not result.get("tool"):
                    result["tool"] = "get_MasterData"

            return RouterDecision(
                action=action,
                tool=result.get("tool"),
                params=result.get("params", {}),
                flow_type=result.get("flow_type"),
                response=result.get("response"),
                reasoning=result.get("reasoning", ""),
                confidence=float(result.get("confidence", 0.5))
            )

        except Exception as e:
            logger.error(f"LLM routing failed: {e}")
            # Fallback - try to detect basic intent
            return self._fallback_route(query, user_context)

    def _fallback_route(
        self,
        query: str,
        user_context: Dict[str, Any]
    ) -> RouterDecision:
        """Fallback routing when LLM fails."""
        query_lower = query.lower()

        # Simple keyword-based fallback
        if any(w in query_lower for w in ["unesi", "upiši", "upisi"]) and "km" in query_lower or "kilometr" in query_lower:
            return RouterDecision(
                action="start_flow",
                tool="post_AddMileage",
                flow_type="mileage",
                reasoning="Fallback: mileage keywords",
                confidence=0.5
            )

        if any(w in query_lower for w in ["šteta", "kvar", "udario", "prijavi"]):
            return RouterDecision(
                action="start_flow",
                tool="post_AddCase",
                flow_type="case",
                reasoning="Fallback: case keywords",
                confidence=0.5
            )

        if any(w in query_lower for w in ["rezerv", "booking", "trebam auto", "trebam vozilo"]):
            return RouterDecision(
                action="start_flow",
                tool="get_AvailableVehicles",
                flow_type="booking",
                reasoning="Fallback: booking keywords",
                confidence=0.5
            )

        if any(w in query_lower for w in ["slobodn", "dostupn"]):
            return RouterDecision(
                action="simple_api",
                tool="get_AvailableVehicles",
                reasoning="Fallback: availability keywords",
                confidence=0.5
            )

        # Default to MasterData for vehicle info
        return RouterDecision(
            action="simple_api",
            tool="get_MasterData",
            reasoning="Fallback: default to vehicle info",
            confidence=0.3
        )


# Singleton
_router: Optional[UnifiedRouter] = None


async def get_unified_router() -> UnifiedRouter:
    """Get or create singleton router instance."""
    global _router
    if _router is None:
        _router = UnifiedRouter()
        await _router.initialize()
    return _router
