"""
Query Router - Deterministic routing for known query patterns.
Version: 1.0

Single responsibility: Route queries to correct tools WITHOUT LLM guessing.
For known patterns, we use RULES, not probabilities.

This guarantees correct responses for common queries.
"""

import re
import logging
from dataclasses import dataclass
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger(__name__)


@dataclass
class RouteResult:
    """Result of query routing."""
    matched: bool
    tool_name: Optional[str] = None
    extract_fields: List[str] = None
    response_template: Optional[str] = None
    flow_type: Optional[str] = None  # "simple", "booking", "mileage_input", etc.
    confidence: float = 1.0
    reason: str = ""

    def __post_init__(self):
        if self.extract_fields is None:
            self.extract_fields = []


class QueryRouter:
    """
    Routes queries to tools using deterministic rules.

    For known patterns:
    - NO embedding search needed
    - NO LLM tool selection needed
    - GUARANTEED correct tool

    This is the "fast path" for common queries.
    """

    def __init__(self):
        """Initialize router with rules."""
        self.rules = self._build_rules()

    def _build_rules(self) -> List[Dict[str, Any]]:
        """Build deterministic routing rules."""
        return [
            # === MILEAGE INPUT (must be BEFORE GET_MILEAGE to catch "unesi" first) ===
            {
                "patterns": [
                    r"unesi.*(km|kilometra)",
                    r"upiši.*(km|kilometra)",
                    r"unos.*(km|kilometra)",
                    r"prijavi.*(km|kilometra)",
                    r"unesite.*(km|kilometra)",
                    r"nova.*kilometra",
                    r"ažuriraj.*(km|kilometra)",
                    r"ho[cć]u.*unijeti.*(km|kilometra)",
                    r"želim.*unijeti.*(km|kilometra)",
                    r"trebam.*unijeti.*(km|kilometra)",
                    r"unijeti.*(km|kilometra)",
                    r"unesi.*\d+",  # "unesi 15000" with number
                ],
                "intent": "INPUT_MILEAGE",
                "tool": "post_AddMileage",
                "extract_fields": [],
                "response_template": None,
                "flow_type": "mileage_input",
            },
            # === MILEAGE QUERIES ===
            {
                "patterns": [
                    r"koliko.*(km|kilometra)",
                    r"kolika.*(km|kilometra)",
                    r"stanje.*(km|kilometra)",
                    r"\bkm\b.*vozil",
                    r"mileage",
                    r"koja.*kilometra[zž]",
                    r"trenutna.*kilometra",
                    r"kilometra[zž]a.*vozil",
                ],
                "intent": "GET_MILEAGE",
                "tool": "get_MasterData",
                "extract_fields": ["LastMileage", "Mileage", "CurrentMileage"],
                "response_template": "📏 **Kilometraža:** {value} km",
                "flow_type": "simple",
            },
            # === REGISTRATION EXPIRY ===
            {
                "patterns": [
                    r"registracij.*isti[cč]e",
                    r"kada.*registracij",
                    r"istje[cč]e.*registracij",
                    r"istek.*registracij",
                    r"do.*kad.*registracij",
                    r"vrijedi.*registracij",
                ],
                "intent": "GET_REGISTRATION_EXPIRY",
                "tool": "get_MasterData",
                "extract_fields": ["RegistrationExpirationDate", "ExpirationDate"],
                "response_template": "📅 **Registracija istječe:** {value}",
                "flow_type": "simple",
            },
            # === VEHICLE INFO ===
            {
                "patterns": [
                    r"podaci.*vozil",
                    r"informacij.*vozil",
                    r"vozilo.*podaci",
                    r"moje.*vozilo",
                    r"koje.*vozilo",
                    r"detalji.*vozil",
                ],
                "intent": "GET_VEHICLE_INFO",
                "tool": "get_MasterData",
                "extract_fields": ["FullVehicleName", "LicencePlate", "LastMileage", "RegistrationExpirationDate"],
                "response_template": None,  # Use LLM extraction for complex response
                "flow_type": "simple",
            },
            # === LICENCE PLATE ===
            {
                "patterns": [
                    r"tablice?",
                    r"registarsk.*oznaka",
                    r"registracij.*broj",
                    r"koje.*tablice",
                ],
                "intent": "GET_PLATE",
                "tool": "get_MasterData",
                "extract_fields": ["LicencePlate", "RegistrationNumber", "Plate"],
                "response_template": "🔢 **Tablica:** {value}",
                "flow_type": "simple",
            },
            # === LEASING ===
            {
                "patterns": [
                    r"lizing",
                    r"leasing",
                    r"koja.*lizing.*ku[cć]a",
                    r"lizing.*provider",
                ],
                "intent": "GET_LEASING",
                "tool": "get_MasterData",
                "extract_fields": ["LeasingProvider", "LeasingCompany", "Leasing"],
                "response_template": "🏢 **Lizing kuća:** {value}",
                "flow_type": "simple",
            },
            # === SERVICE / MAINTENANCE ===
            {
                "patterns": [
                    r"servis",                          # servis
                    r"koliko.*do.*servis",              # koliko do servisa
                    r"kad.*servis",                     # kad je servis, kad trebam na servis
                    r"kada.*servis",                    # kada je servis
                    r"sljede[cć]i.*servis",             # sljedeći servis
                    r"trebam.*servis",                  # trebam na servis
                    r"preostalo.*servis",               # preostalo do servisa
                    r"do.*servisa",                     # do servisa
                    r"odr[zž]avanj",                    # održavanje/odrzavanje
                    r"zadnji.*servis",                  # zadnji servis
                    r"pro[sš]li.*servis",               # prošli/prosli servis
                    r"povijest.*servis",                # povijest servisa
                ],
                "intent": "GET_SERVICE_MILEAGE",
                "tool": "get_MasterData",
                "extract_fields": ["ServiceMileage", "NextServiceMileage", "LastServiceDate"],
                "response_template": "🔧 **Do servisa:** {value} km",
                "flow_type": "simple",
            },
            # === MY BOOKINGS (must be BEFORE booking to catch "moje rezervacije" first) ===
            {
                "patterns": [
                    r"moje.*rezervacij",
                    r"moje.*booking",
                    r"kada.*imam.*rezerv",
                    r"poka[zž]i.*rezervacij",  # pokaži/pokazi rezervacije
                    r"prika[zž]i.*rezervacij", # prikaži/prikazi rezervacije
                    r"sve.*rezervacij",        # sve rezervacije
                    r"ima[lm].*rezerv",        # imam/imali rezervaciju
                ],
                "intent": "GET_MY_BOOKINGS",
                "tool": "get_VehicleCalendar",
                "extract_fields": ["FromTime", "ToTime", "VehicleName"],
                "response_template": None,
                "flow_type": "list",
            },
            # === BOOKING / RESERVATION ===
            {
                "patterns": [
                    r"rezervir",
                    r"rezervacij",  # Note: "moje rezervacije" caught by MY_BOOKINGS above
                    r"trebam.*vozilo",
                    r"treba.*mi.*vozilo",
                    r"book(?!ing)",           # book but not booking (for "moje booking")
                    r"zauzmi",
                    r"zakup",
                    r"ho[cć]u.*rezerv",       # hoću rezervirati
                    r"[zž]elim.*rezerv",      # želim rezervirati
                ],
                "intent": "BOOK_VEHICLE",
                "tool": "get_AvailableVehicles",
                "extract_fields": [],
                "response_template": None,
                "flow_type": "booking",
            },
            # === REPORT DAMAGE ===
            {
                "patterns": [
                    r"prijavi.*kvar",
                    r"prijava.*kvar",
                    r"prijavi.*[sš]tet",    # prijavi štetu, prijavi stetu (with/without diacritics)
                    r"prijava.*[sš]tet",    # prijava štete, prijava stete
                    r"[sš]tet[aeu]",        # šteta/steta, štetu/stetu, štete/stete
                    r"o[sš]te[cć]enj",      # oštećenje, ostecenje
                    r"ne[sš]to.*ne.*radi",
                    r"problem.*vozil",
                    r"kvar",
                    r"imam.*kvar",          # imam kvar
                    r"imam.*[sš]tet",       # imam štetu/stetu
                    r"ima.*[sš]tet",        # ima štete/stete
                    r"dogodila.*nesre[cć]", # dogodila se nesreća/nesreca
                    r"nesre[cć]",           # nesreća/nesreca
                    r"sudar",               # sudar
                    r"udar",                # udar
                ],
                "intent": "REPORT_DAMAGE",
                "tool": "post_AddCase",
                "extract_fields": [],
                "response_template": None,
                "flow_type": "case_creation",
            },
            # === GREETINGS ===
            {
                "patterns": [
                    r"^bok$",
                    r"^cao$",
                    r"^pozdrav$",
                    r"^zdravo$",
                    r"^hej$",
                    r"^hi$",
                    r"^hello$",
                ],
                "intent": "GREETING",
                "tool": None,
                "extract_fields": [],
                "response_template": "Pozdrav! Kako vam mogu pomoći?",
                "flow_type": "direct_response",
            },
            # === THANKS ===
            {
                "patterns": [
                    r"hvala",
                    r"zahvalju",
                    r"thanks",
                    r"fala",
                ],
                "intent": "THANKS",
                "tool": None,
                "extract_fields": [],
                "response_template": "Nema na čemu! Slobodno pitajte ako trebate još nešto.",
                "flow_type": "direct_response",
            },
            # === HELP ===
            {
                "patterns": [
                    r"^pomo[cć]$",
                    r"^help$",
                    r"što.*može[sš]",
                    r"kako.*koristiti",
                    r"što.*zna[sš]",
                ],
                "intent": "HELP",
                "tool": None,
                "extract_fields": [],
                "response_template": (
                    "Mogu vam pomoći s:\n"
                    "• **Kilometraža** - provjera ili unos km\n"
                    "• **Rezervacije** - rezervacija vozila\n"
                    "• **Podaci o vozilu** - registracija, lizing\n"
                    "• **Prijava kvara** - kreiranje slučaja\n\n"
                    "Što vas zanima?"
                ),
                "flow_type": "direct_response",
            },
        ]

    def route(self, query: str, user_context: Optional[Dict[str, Any]] = None) -> RouteResult:
        """
        Route query to appropriate tool.

        Args:
            query: User's query text
            user_context: Optional user context

        Returns:
            RouteResult with matched tool or not matched
        """
        query_lower = query.lower().strip()

        for rule in self.rules:
            for pattern in rule["patterns"]:
                if re.search(pattern, query_lower, re.IGNORECASE):
                    logger.info(
                        f"ROUTER: Matched '{query[:30]}...' to {rule['intent']} "
                        f"→ {rule['tool'] or 'direct_response'}"
                    )

                    return RouteResult(
                        matched=True,
                        tool_name=rule["tool"],
                        extract_fields=rule["extract_fields"],
                        response_template=rule["response_template"],
                        flow_type=rule["flow_type"],
                        confidence=1.0,
                        reason=f"Matched pattern: {pattern}"
                    )

        # No exact match - let semantic search handle it
        logger.info(f"ROUTER: No match for '{query[:30]}...' - using semantic search")
        return RouteResult(
            matched=False,
            confidence=0.0,
            reason="No pattern matched, no domain detected"
        )


    def format_response(
        self,
        route: RouteResult,
        api_response: Dict[str, Any],
        query: str
    ) -> Optional[str]:
        """
        Format response using template if available.

        Args:
            route: The route result with template
            api_response: Raw API response
            query: Original query

        Returns:
            Formatted response string or None if should use LLM
        """
        if not route.response_template:
            return None

        if not route.extract_fields:
            return route.response_template

        # Try to extract value
        value = self._extract_value(api_response, route.extract_fields)

        if value is None:
            return None  # Let LLM handle it

        # Format value based on field type
        formatted_value = self._format_value(value, route.extract_fields[0])

        return route.response_template.format(value=formatted_value)

    def _extract_value(
        self,
        data: Dict[str, Any],
        fields: List[str]
    ) -> Optional[Any]:
        """Extract value from response using field list."""
        if not data:
            return None

        # Try each field
        for field in fields:
            # Direct match
            if field in data and data[field] is not None:
                return data[field]

            # Nested search
            value = self._deep_get(data, field)
            if value is not None:
                return value

        return None

    def _deep_get(self, data: Any, key: str) -> Optional[Any]:
        """Recursively search for key in nested dict."""
        if isinstance(data, dict):
            if key in data:
                return data[key]
            for v in data.values():
                result = self._deep_get(v, key)
                if result is not None:
                    return result
        elif isinstance(data, list) and data:
            return self._deep_get(data[0], key)
        return None

    def _format_value(self, value: Any, field_name: str) -> str:
        """Format value based on field type."""
        if value is None:
            return "N/A"

        field_lower = field_name.lower()

        # Mileage - add thousand separators
        if "mileage" in field_lower:
            try:
                num = int(float(value))
                return f"{num:,}".replace(",", ".")
            except (ValueError, TypeError):
                return str(value)

        # Date - format as DD.MM.YYYY
        if "date" in field_lower or "expir" in field_lower:
            if isinstance(value, str) and "T" in value:
                try:
                    date_part = value.split("T")[0]
                    parts = date_part.split("-")
                    if len(parts) == 3:
                        return f"{parts[2]}.{parts[1]}.{parts[0]}"
                except:
                    pass
            return str(value)

        return str(value)


# Singleton
_router = None


def get_query_router() -> QueryRouter:
    """Get singleton instance."""
    global _router
    if _router is None:
        _router = QueryRouter()
    return _router
