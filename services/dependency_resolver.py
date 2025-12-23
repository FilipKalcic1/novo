"""
Dependency Resolver - Automatic Tool Chaining Engine
Version: 2.0

KRITIČNA KOMPONENTA za 10/10 ocjenu sustava.

Rješava probleme:
1. Korisnik ne zna UUID-ove (VehicleId, PersonId, etc.)
2. Korisnik daje human-readable vrijednosti (registracija, ime)
3. Sustav mora automatski resolvati dependencies
4. NEW v2.0: Entity Resolution za tekstualne reference ("Vozilo 1", "moje vozilo")

Primjer 1 (registracija):
    Korisnik: "Rezerviraj vozilo ZG-1234-AB za sutra"
    → Detektira registraciju → get_Vehicles(Filter=LicencePlate) → UUID

Primjer 2 (tekstualna referenca):
    Korisnik: "Dodaj kilometražu na Vozilo 1"
    → Detektira "Vozilo 1" → get_MasterData (ili korisnikov popis) → UUID

Primjer 3 (posvojne reference):
    Korisnik: "Koja je kilometraža na mom vozilu"
    → Detektira "moje vozilo" → koristi user_context.vehicle.id → UUID

NO HARDCODING - sve se temelji na DependencyGraph i output_keys.
"""

import logging
import re
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ResolutionResult:
    """Result of dependency resolution."""
    success: bool
    resolved_value: Optional[Any] = None
    provider_tool: Optional[str] = None
    provider_params: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None


@dataclass
class ValuePattern:
    """Pattern for recognizing human-readable values."""
    pattern: str  # Regex pattern
    param_type: str  # What parameter type this resolves to
    filter_field: str  # API filter field name
    description: str  # Human description


@dataclass
class EntityReference:
    """
    Detected entity reference from user text.

    NEW v2.0: Enables "Vozilo 1" → UUID resolution.
    """
    entity_type: str  # "vehicle", "person", "location"
    reference_type: str  # "ordinal", "possessive", "name", "pattern"
    value: str  # Original text ("Vozilo 1", "moje vozilo", "Golf")
    ordinal_index: Optional[int] = None  # For "Vozilo 1" → 0 (0-indexed)
    is_possessive: bool = False  # For "moje vozilo", "moj auto"


class DependencyResolver:
    """
    Resolves parameter dependencies automatically.

    Features:
    - Pattern recognition for human-readable values
    - Automatic provider tool selection
    - Filter query construction
    - Caching of resolved values
    - NEW v2.0: Entity Resolution for textual references

    KRITIČNO: Ovo omogućava da korisnik kaže "ZG-1234-AB"
              ili "Vozilo 1" ili "moje vozilo" umjesto UUID-a!
    """

    # ═══════════════════════════════════════════════════════════════
    # ENTITY REFERENCE PATTERNS (NEW v2.0)
    # ═══════════════════════════════════════════════════════════════

    # Ordinal patterns: "Vozilo 1", "Vozilo 2", "Vehicle 1"
    ORDINAL_PATTERNS: List[Tuple[str, str]] = [
        # Croatian
        (r'vozilo\s*(\d+)', 'vehicle'),
        (r'auto\s*(\d+)', 'vehicle'),
        (r'automobil\s*(\d+)', 'vehicle'),
        # English
        (r'vehicle\s*(\d+)', 'vehicle'),
        (r'car\s*(\d+)', 'vehicle'),
        # Generic numbered
        (r'#(\d+)\s*vozilo', 'vehicle'),
        (r'broj\s*(\d+)', 'vehicle'),
    ]

    # Possessive patterns: "moje vozilo", "moj auto", "my car"
    POSSESSIVE_PATTERNS: List[Tuple[str, str]] = [
        # Croatian possessives (nominative/accusative)
        (r'moje?\s+vozilo', 'vehicle'),
        (r'moje?\s+auto', 'vehicle'),
        (r'moje?\s+automobil', 'vehicle'),
        (r'moje?\s+registracij[au]', 'vehicle'),
        # Croatian possessives (dative/locative - "na mom vozilu", "mom autu")
        (r'mom(?:e|u|em)?\s+vozil[ua]', 'vehicle'),
        (r'mom(?:e|u|em)?\s+aut[ua]', 'vehicle'),
        (r'mojoj?e?m?\s+registracij[iu]', 'vehicle'),
        # Additional Croatian forms
        (r'moj(?:em?)?\s+vozil[ua]', 'vehicle'),
        # English possessives
        (r'my\s+vehicle', 'vehicle'),
        (r'my\s+car', 'vehicle'),
        # Implicit possessive (just "vozilo" in context of user's data)
        (r'^vozilo$', 'vehicle'),
        (r'^auto$', 'vehicle'),
    ]

    # Common vehicle name patterns (brands, models)
    VEHICLE_NAME_PATTERNS: List[str] = [
        # VW
        r'golf', r'passat', r'polo', r'tiguan', r'touran', r'caddy', r'transporter', r't-roc', r'arteon',
        # Škoda
        r'octavia', r'fabia', r'superb', r'kodiaq', r'karoq', r'scala', r'kamiq',
        # Audi
        r'a3', r'a4', r'a5', r'a6', r'a7', r'a8', r'q3', r'q5', r'q7', r'q8',
        # BMW
        r'serija\s*[1-8]', r'x[1-7]', r'z4', r'i[34x]',
        # Mercedes
        r'a\s*klasa', r'b\s*klasa', r'c\s*klasa', r'e\s*klasa', r's\s*klasa', r'gle', r'glc', r'gla',
        # Renault
        r'megane', r'clio', r'captur', r'kadjar', r'scenic', r'kangoo', r'master', r'trafic',
        # Ford
        r'focus', r'fiesta', r'mondeo', r'kuga', r'puma', r'transit',
        # Opel
        r'astra', r'corsa', r'insignia', r'mokka', r'crossland', r'grandland',
        # Toyota
        r'yaris', r'corolla', r'rav4', r'c-hr', r'aygo', r'camry', r'proace',
        # Peugeot
        r'208', r'308', r'508', r'2008', r'3008', r'5008', r'partner', r'expert',
        # Citroen
        r'c3', r'c4', r'c5', r'berlingo', r'jumpy', r'jumper',
        # Fiat
        r'tipo', r'500', r'panda', r'punto', r'doblo', r'ducato',
        # Hyundai/Kia
        r'i10', r'i20', r'i30', r'tucson', r'kona', r'santa\s*fe',
        r'ceed', r'sportage', r'niro', r'stonic', r'xceed',
        # Dacia
        r'duster', r'sandero', r'logan', r'dokker', r'spring',
    ]

    # Patterns for recognizing human-readable values
    # Format: (regex, target_param, filter_field, description)
    VALUE_PATTERNS: List[ValuePattern] = [
        # Croatian license plates: ZG-1234-AB, ZG 1234 AB, ZG1234AB
        ValuePattern(
            pattern=r'^[A-ZČĆŽŠĐ]{2}[\s\-]?\d{3,4}[\s\-]?[A-ZČĆŽŠĐ]{1,2}$',
            param_type='vehicleid',
            filter_field='LicencePlate',
            description='Croatian license plate'
        ),
        # VIN numbers (17 characters)
        ValuePattern(
            pattern=r'^[A-HJ-NPR-Z0-9]{17}$',
            param_type='vehicleid',
            filter_field='VIN',
            description='Vehicle VIN'
        ),
        # Email addresses
        ValuePattern(
            pattern=r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$',
            param_type='personid',
            filter_field='Email',
            description='Email address'
        ),
        # Phone numbers (Croatian format)
        ValuePattern(
            pattern=r'^(\+385|00385|0)?[1-9]\d{7,8}$',
            param_type='personid',
            filter_field='Phone',
            description='Phone number'
        ),
    ]

    # Mapping: parameter name → provider tool patterns
    # These are semantic mappings, not hardcoded tool names
    PARAM_PROVIDERS: Dict[str, Dict[str, Any]] = {
        'vehicleid': {
            'search_terms': ['vehicle', 'vehicles', 'masterdata'],
            'output_keys': ['Id', 'VehicleId', 'vehicleId'],
            'preferred_method': 'GET',
        },
        'personid': {
            'search_terms': ['person', 'persons', 'driver', 'user'],
            'output_keys': ['Id', 'PersonId', 'personId', 'UserId'],
            'preferred_method': 'GET',
        },
        'locationid': {
            'search_terms': ['location', 'locations', 'site'],
            'output_keys': ['Id', 'LocationId', 'locationId'],
            'preferred_method': 'GET',
        },
        'bookingid': {
            'search_terms': ['booking', 'calendar', 'reservation'],
            'output_keys': ['Id', 'BookingId', 'bookingId'],
            'preferred_method': 'GET',
        },
    }

    def __init__(self, registry: Any):
        """
        Initialize resolver with tool registry.

        Args:
            registry: ToolRegistry instance for finding provider tools
        """
        self.registry = registry
        self._resolution_cache: Dict[str, Any] = {}

        logger.info("DependencyResolver initialized")

    def detect_value_type(self, value: str) -> Optional[Tuple[str, str]]:
        """
        Detect what type of value the user provided.

        Args:
            value: User-provided value (e.g., "ZG-1234-AB")

        Returns:
            Tuple of (param_type, filter_field) or None

        Example:
            "ZG-1234-AB" → ("vehicleid", "LicencePlate")
        """
        if not value or not isinstance(value, str):
            return None

        value_upper = value.upper().strip()

        for pattern in self.VALUE_PATTERNS:
            if re.match(pattern.pattern, value_upper, re.IGNORECASE):
                logger.info(
                    f"🔍 Detected {pattern.description}: '{value}' "
                    f"→ {pattern.param_type}.{pattern.filter_field}"
                )
                return (pattern.param_type, pattern.filter_field)

        return None

    def find_provider_tool(
        self,
        missing_param: str,
        available_tools: Optional[List[str]] = None
    ) -> Optional[str]:
        """
        Find a tool that can provide the missing parameter.

        Uses DependencyGraph from registry if available,
        otherwise falls back to semantic search.

        Args:
            missing_param: Name of missing parameter (e.g., "VehicleId")
            available_tools: Optional list of already-loaded tools

        Returns:
            Tool operation_id that can provide this parameter
        """
        param_lower = missing_param.lower()

        # Normalize param name (remove common suffixes)
        base_param = param_lower.replace('id', '').replace('_', '')

        # Check if we have provider config for this param type
        provider_config = None
        for param_type, config in self.PARAM_PROVIDERS.items():
            if param_type.replace('id', '') == base_param or param_lower == param_type:
                provider_config = config
                break

        if not provider_config:
            logger.warning(f"No provider config for param: {missing_param}")
            return None

        # Strategy 1: Check DependencyGraph
        if hasattr(self.registry, 'dependency_graph'):
            for tool_id, dep_graph in self.registry.dependency_graph.items():
                if missing_param in dep_graph.provider_tools:
                    logger.info(f"📦 Found provider via DependencyGraph: {tool_id}")
                    return tool_id

        # Strategy 2: Search tools by output_keys
        for tool_id, tool in self.registry.tools.items():
            # Check if tool provides any of the expected output keys
            tool_outputs_lower = [k.lower() for k in tool.output_keys]

            for expected_key in provider_config['output_keys']:
                if expected_key.lower() in tool_outputs_lower:
                    # Prefer GET methods for lookups
                    if tool.method == provider_config.get('preferred_method', 'GET'):
                        logger.info(
                            f"📦 Found provider via output_keys: {tool_id} "
                            f"(provides {expected_key})"
                        )
                        return tool_id

        # Strategy 3: Search by name patterns
        for tool_id, tool in self.registry.tools.items():
            tool_id_lower = tool_id.lower()

            for search_term in provider_config['search_terms']:
                if search_term in tool_id_lower:
                    if tool.method == provider_config.get('preferred_method', 'GET'):
                        # Avoid delete/update tools
                        if not any(x in tool_id_lower for x in ['delete', 'remove', 'update', 'put', 'patch']):
                            logger.info(
                                f"📦 Found provider via name pattern: {tool_id}"
                            )
                            return tool_id

        logger.warning(f"❌ No provider found for: {missing_param}")
        return None

    def build_filter_query(
        self,
        filter_field: str,
        value: str
    ) -> Dict[str, Any]:
        """
        Build API filter query for the provider tool.

        Args:
            filter_field: Field to filter on (e.g., "LicencePlate")
            value: Value to filter by (e.g., "ZG-1234-AB")

        Returns:
            Dict with filter parameters
        """
        # Standard OData-style filter
        # Format depends on API, but most common patterns:

        # Clean the value (remove extra spaces, normalize)
        clean_value = value.strip()

        # Build filter string
        # Most APIs use: Filter=Field(=)Value or Filter=Field eq 'Value'
        filter_params = {
            'Filter': f"{filter_field}(=){clean_value}"
        }

        logger.debug(f"Built filter query: {filter_params}")

        return filter_params

    async def resolve_dependency(
        self,
        missing_param: str,
        user_value: Optional[str],
        user_context: Dict[str, Any],
        executor: Any
    ) -> ResolutionResult:
        """
        Resolve a missing parameter by calling provider tool.

        This is the MAIN METHOD for automatic chaining.

        Args:
            missing_param: Name of missing parameter
            user_value: Optional user-provided value (e.g., "ZG-1234-AB")
            user_context: User context with tenant_id, person_id, etc.
            executor: ToolExecutor for calling provider

        Returns:
            ResolutionResult with resolved value or error
        """
        logger.info(f"🔗 Resolving dependency: {missing_param}")

        # Check cache first
        cache_key = f"{missing_param}:{user_value}"
        if cache_key in self._resolution_cache:
            cached = self._resolution_cache[cache_key]
            logger.info(f"✅ Cache hit for {cache_key}")
            return ResolutionResult(
                success=True,
                resolved_value=cached['value'],
                provider_tool=cached['tool']
            )

        # Find provider tool
        provider_tool_id = self.find_provider_tool(missing_param)

        if not provider_tool_id:
            return ResolutionResult(
                success=False,
                error_message=f"Ne mogu pronaći način za dohvatiti {missing_param}"
            )

        provider_tool = self.registry.get_tool(provider_tool_id)
        if not provider_tool:
            return ResolutionResult(
                success=False,
                error_message=f"Provider tool {provider_tool_id} nije dostupan"
            )

        # Build parameters for provider
        provider_params = {}

        # If user provided a value, try to use it as filter
        if user_value:
            value_type = self.detect_value_type(user_value)

            if value_type:
                param_type, filter_field = value_type
                provider_params = self.build_filter_query(filter_field, user_value)
            else:
                # Try as generic search/name filter
                provider_params = {'Filter': f"Name(~){user_value}"}

        # Add context params that provider might need
        if 'person_id' in user_context:
            # Some tools filter by person
            for param_name in ['PersonId', 'personId', 'DriverId', 'driverId']:
                if param_name.lower() in [p.lower() for p in provider_tool.parameters.keys()]:
                    provider_params[param_name] = user_context['person_id']
                    break

        # Execute provider tool
        try:
            from services.tool_contracts import ToolExecutionContext

            exec_context = ToolExecutionContext(
                user_context=user_context,
                tool_outputs={},
                conversation_state={}
            )

            result = await executor.execute(
                tool=provider_tool,
                llm_params=provider_params,
                execution_context=exec_context
            )

            if not result.success:
                return ResolutionResult(
                    success=False,
                    provider_tool=provider_tool_id,
                    provider_params=provider_params,
                    error_message=result.error_message
                )

            # Extract the ID we need from result
            resolved_value = self._extract_id_from_result(
                result.data,
                missing_param
            )

            if resolved_value:
                # Cache the resolution
                self._resolution_cache[cache_key] = {
                    'value': resolved_value,
                    'tool': provider_tool_id
                }

                logger.info(
                    f"✅ Resolved {missing_param} = {resolved_value} "
                    f"via {provider_tool_id}"
                )

                return ResolutionResult(
                    success=True,
                    resolved_value=resolved_value,
                    provider_tool=provider_tool_id,
                    provider_params=provider_params
                )
            else:
                return ResolutionResult(
                    success=False,
                    provider_tool=provider_tool_id,
                    error_message=f"Provider {provider_tool_id} nije vratio {missing_param}"
                )

        except Exception as e:
            logger.error(f"Resolution error: {e}", exc_info=True)
            return ResolutionResult(
                success=False,
                error_message=f"Greška pri resolvanju: {str(e)}"
            )

    def _extract_id_from_result(
        self,
        data: Any,
        param_name: str
    ) -> Optional[str]:
        """
        Extract ID value from API response.

        Handles various response formats:
        - Direct dict: {"Id": "123"}
        - Array: [{"Id": "123"}]
        - Wrapped: {"Data": [{"Id": "123"}]}
        - Items: {"Items": [{"Id": "123"}]}
        """
        if not data:
            return None

        # Possible key names for the ID
        param_lower = param_name.lower()
        possible_keys = [
            param_name,
            param_name.replace('Id', ''),
            'Id', 'id', 'ID',
            'VehicleId', 'vehicleId', 'vehicle_id',
            'PersonId', 'personId', 'person_id',
        ]

        def extract_from_dict(d: dict) -> Optional[str]:
            for key in possible_keys:
                if key in d and d[key]:
                    return str(d[key])
            # Case insensitive search
            for k, v in d.items():
                if k.lower() == param_lower or k.lower() == 'id':
                    if v:
                        return str(v)
            return None

        # Handle different response structures
        if isinstance(data, dict):
            # Try direct extraction
            result = extract_from_dict(data)
            if result:
                return result

            # Try nested structures
            for wrapper_key in ['Data', 'data', 'Items', 'items', 'Results', 'results']:
                if wrapper_key in data:
                    nested = data[wrapper_key]
                    if isinstance(nested, list) and nested:
                        return extract_from_dict(nested[0])
                    elif isinstance(nested, dict):
                        return extract_from_dict(nested)

        elif isinstance(data, list) and data:
            return extract_from_dict(data[0])

        return None

    def clear_cache(self) -> None:
        """Clear resolution cache."""
        self._resolution_cache.clear()
        logger.info("Resolution cache cleared")

    # ═══════════════════════════════════════════════════════════════
    # ENTITY RESOLUTION (NEW v2.0)
    # ═══════════════════════════════════════════════════════════════

    def detect_entity_reference(
        self,
        text: str,
        entity_type: str = "vehicle"
    ) -> Optional[EntityReference]:
        """
        Detect entity reference in user text.

        NEW v2.0: Prepoznaje tekstualne reference poput:
        - "Vozilo 1" → ordinal (index 0)
        - "moje vozilo" → possessive (user's default)
        - "Golf" → name match

        Args:
            text: User input text
            entity_type: Type to search for ("vehicle", "person")

        Returns:
            EntityReference or None

        Example:
            detect_entity_reference("Dodaj km na Vozilo 1")
            → EntityReference(type="vehicle", reference_type="ordinal", ordinal_index=0)
        """
        if not text:
            return None

        text_lower = text.lower().strip()

        # 1. Check ordinal patterns ("Vozilo 1", "Auto 2")
        for pattern, p_type in self.ORDINAL_PATTERNS:
            if p_type != entity_type:
                continue

            match = re.search(pattern, text_lower, re.IGNORECASE)
            if match:
                ordinal = int(match.group(1))
                # Convert 1-indexed to 0-indexed
                ordinal_index = ordinal - 1 if ordinal > 0 else 0

                logger.info(
                    f"🔢 Detected ordinal reference: '{text}' → "
                    f"{entity_type}[{ordinal_index}]"
                )

                return EntityReference(
                    entity_type=entity_type,
                    reference_type="ordinal",
                    value=match.group(0),
                    ordinal_index=ordinal_index,
                    is_possessive=False
                )

        # 2. Check possessive patterns ("moje vozilo", "moj auto")
        for pattern, p_type in self.POSSESSIVE_PATTERNS:
            if p_type != entity_type:
                continue

            match = re.search(pattern, text_lower, re.IGNORECASE)
            if match:
                logger.info(
                    f"👤 Detected possessive reference: '{text}' → "
                    f"user's {entity_type}"
                )

                return EntityReference(
                    entity_type=entity_type,
                    reference_type="possessive",
                    value=match.group(0),
                    ordinal_index=0,  # Default to first/primary
                    is_possessive=True
                )

        # 3. Check vehicle name patterns (Golf, Passat, etc.)
        if entity_type == "vehicle":
            for name_pattern in self.VEHICLE_NAME_PATTERNS:
                match = re.search(name_pattern, text_lower, re.IGNORECASE)
                if match:
                    logger.info(
                        f"🚗 Detected vehicle name: '{match.group(0)}'"
                    )

                    return EntityReference(
                        entity_type="vehicle",
                        reference_type="name",
                        value=match.group(0),
                        ordinal_index=None,
                        is_possessive=False
                    )

        return None

    async def resolve_entity_reference(
        self,
        reference: EntityReference,
        user_context: Dict[str, Any],
        executor: Any
    ) -> ResolutionResult:
        """
        Resolve entity reference to actual UUID.

        NEW v2.0: Koristi MasterData ili user_context za resolvanje.

        Flow:
        1. Ako je "possessive" ("moje vozilo"):
           → Koristi user_context.vehicle.id
        2. Ako je "ordinal" ("Vozilo 1"):
           → Dohvati listu vozila za korisnika
           → Vrati vozilo[ordinal_index]
        3. Ako je "name" ("Golf"):
           → Pretraži vozila po imenu/opisu

        Args:
            reference: Detected EntityReference
            user_context: User context with vehicle info
            executor: ToolExecutor for API calls

        Returns:
            ResolutionResult with UUID or error
        """
        logger.info(f"🔍 Resolving entity: {reference}")

        # STRATEGY 1: Possessive - use user's default vehicle
        if reference.is_possessive or reference.reference_type == "possessive":
            vehicle = user_context.get("vehicle", {})
            vehicle_id = vehicle.get("id") or vehicle.get("vehicle_id")

            if vehicle_id:
                logger.info(
                    f"✅ Resolved possessive to user's vehicle: {vehicle_id}"
                )
                return ResolutionResult(
                    success=True,
                    resolved_value=vehicle_id,
                    provider_tool="user_context",
                    provider_params={"source": "possessive"}
                )
            else:
                # No default vehicle - try to fetch user's vehicles
                logger.info("User has no default vehicle, fetching list...")
                reference = EntityReference(
                    entity_type="vehicle",
                    reference_type="ordinal",
                    value=reference.value,
                    ordinal_index=0,  # First vehicle
                    is_possessive=False
                )
                # Fall through to ordinal resolution

        # STRATEGY 2: Ordinal - fetch list and pick by index
        if reference.reference_type == "ordinal":
            return await self._resolve_by_ordinal(
                reference, user_context, executor
            )

        # STRATEGY 3: Name - search by vehicle name/description
        if reference.reference_type == "name":
            return await self._resolve_by_name(
                reference, user_context, executor
            )

        return ResolutionResult(
            success=False,
            error_message=f"Ne mogu resolvirati referencu: {reference.value}"
        )

    async def _resolve_by_ordinal(
        self,
        reference: EntityReference,
        user_context: Dict[str, Any],
        executor: Any
    ) -> ResolutionResult:
        """
        Resolve ordinal reference ("Vozilo 1") by fetching user's vehicle list.

        Uses get_MasterData ili get_Vehicles s PersonId filterom.
        """
        # Find provider tool for listing vehicles
        provider_tool_id = self.find_provider_tool("VehicleId")

        if not provider_tool_id:
            # Try to use get_MasterData as fallback
            for tool_id in self.registry.tools.keys():
                if "masterdata" in tool_id.lower():
                    provider_tool_id = tool_id
                    break

        if not provider_tool_id:
            return ResolutionResult(
                success=False,
                error_message="Ne mogu pronaći alat za dohvat vozila"
            )

        provider_tool = self.registry.get_tool(provider_tool_id)
        if not provider_tool:
            return ResolutionResult(
                success=False,
                error_message=f"Alat {provider_tool_id} nije dostupan"
            )

        # Build params - filter by person if available
        provider_params = {}
        person_id = user_context.get("person_id")
        if person_id:
            # Try different param names
            for param_name in ["PersonId", "personId", "DriverId", "driverId", "AssignedToId"]:
                if param_name.lower() in [p.lower() for p in provider_tool.parameters.keys()]:
                    provider_params[param_name] = person_id
                    break

        try:
            from services.tool_contracts import ToolExecutionContext

            exec_context = ToolExecutionContext(
                user_context=user_context,
                tool_outputs={},
                conversation_state={}
            )

            result = await executor.execute(
                tool=provider_tool,
                llm_params=provider_params,
                execution_context=exec_context
            )

            if not result.success:
                return ResolutionResult(
                    success=False,
                    provider_tool=provider_tool_id,
                    error_message=result.error_message
                )

            # Extract vehicle list
            vehicles = self._extract_vehicle_list(result.data)

            if not vehicles:
                return ResolutionResult(
                    success=False,
                    provider_tool=provider_tool_id,
                    error_message="Nema dostupnih vozila"
                )

            # Get vehicle by ordinal index
            index = reference.ordinal_index or 0
            if index < 0:
                index = 0
            if index >= len(vehicles):
                return ResolutionResult(
                    success=False,
                    provider_tool=provider_tool_id,
                    error_message=(
                        f"Vozilo {index + 1} ne postoji. "
                        f"Dostupno je {len(vehicles)} vozila."
                    )
                )

            vehicle = vehicles[index]
            vehicle_id = self._extract_id_from_result(vehicle, "VehicleId")

            if vehicle_id:
                # Cache for future use
                cache_key = f"ordinal:{reference.value}"
                self._resolution_cache[cache_key] = {
                    "value": vehicle_id,
                    "tool": provider_tool_id
                }

                # Log which vehicle was selected
                vehicle_name = (
                    vehicle.get("FullVehicleName") or
                    vehicle.get("Name") or
                    vehicle.get("DisplayName") or
                    f"Vozilo {index + 1}"
                )
                plate = vehicle.get("LicencePlate") or vehicle.get("Plate", "")

                logger.info(
                    f"✅ Resolved ordinal {index + 1} → "
                    f"{vehicle_name} ({plate}) = {vehicle_id}"
                )

                return ResolutionResult(
                    success=True,
                    resolved_value=vehicle_id,
                    provider_tool=provider_tool_id,
                    provider_params={"ordinal": index + 1}
                )

            return ResolutionResult(
                success=False,
                provider_tool=provider_tool_id,
                error_message="Nije moguće izvući ID vozila"
            )

        except Exception as e:
            logger.error(f"Ordinal resolution error: {e}", exc_info=True)
            return ResolutionResult(
                success=False,
                error_message=f"Greška: {str(e)}"
            )

    async def _resolve_by_name(
        self,
        reference: EntityReference,
        user_context: Dict[str, Any],
        executor: Any
    ) -> ResolutionResult:
        """
        Resolve name reference ("Golf", "Passat") by searching vehicles.

        Uses Filter parameter with name matching.
        """
        # Find provider tool
        provider_tool_id = self.find_provider_tool("VehicleId")

        if not provider_tool_id:
            for tool_id in self.registry.tools.keys():
                if "masterdata" in tool_id.lower():
                    provider_tool_id = tool_id
                    break

        if not provider_tool_id:
            return ResolutionResult(
                success=False,
                error_message="Ne mogu pronaći alat za pretragu vozila"
            )

        provider_tool = self.registry.get_tool(provider_tool_id)
        if not provider_tool:
            return ResolutionResult(
                success=False,
                error_message=f"Alat {provider_tool_id} nije dostupan"
            )

        # Build filter for name search
        search_value = reference.value.strip()
        provider_params = {
            "Filter": f"Name(~){search_value}"
        }

        # Also add person filter if available
        person_id = user_context.get("person_id")
        if person_id:
            for param_name in ["PersonId", "personId", "DriverId"]:
                if param_name.lower() in [p.lower() for p in provider_tool.parameters.keys()]:
                    provider_params[param_name] = person_id
                    break

        try:
            from services.tool_contracts import ToolExecutionContext

            exec_context = ToolExecutionContext(
                user_context=user_context,
                tool_outputs={},
                conversation_state={}
            )

            result = await executor.execute(
                tool=provider_tool,
                llm_params=provider_params,
                execution_context=exec_context
            )

            if not result.success:
                # Try without filter (just get all and search locally)
                result = await executor.execute(
                    tool=provider_tool,
                    llm_params={},
                    execution_context=exec_context
                )

            if not result.success:
                return ResolutionResult(
                    success=False,
                    provider_tool=provider_tool_id,
                    error_message=result.error_message
                )

            # Search in results
            vehicles = self._extract_vehicle_list(result.data)
            matched_vehicle = self._fuzzy_match_vehicle(vehicles, search_value)

            if matched_vehicle:
                vehicle_id = self._extract_id_from_result(matched_vehicle, "VehicleId")

                if vehicle_id:
                    vehicle_name = (
                        matched_vehicle.get("FullVehicleName") or
                        matched_vehicle.get("Name") or
                        search_value
                    )

                    logger.info(
                        f"✅ Resolved name '{search_value}' → "
                        f"{vehicle_name} = {vehicle_id}"
                    )

                    return ResolutionResult(
                        success=True,
                        resolved_value=vehicle_id,
                        provider_tool=provider_tool_id,
                        provider_params={"name": search_value}
                    )

            return ResolutionResult(
                success=False,
                provider_tool=provider_tool_id,
                error_message=f"Vozilo '{search_value}' nije pronađeno"
            )

        except Exception as e:
            logger.error(f"Name resolution error: {e}", exc_info=True)
            return ResolutionResult(
                success=False,
                error_message=f"Greška: {str(e)}"
            )

    def _extract_vehicle_list(self, data: Any) -> List[Dict[str, Any]]:
        """
        Extract list of vehicles from API response.

        Handles various response structures.
        """
        if not data:
            return []

        # Direct list
        if isinstance(data, list):
            return data

        # Wrapped response
        if isinstance(data, dict):
            for key in ["Data", "data", "Items", "items", "Results", "results", "value"]:
                if key in data:
                    items = data[key]
                    if isinstance(items, list):
                        return items

            # Single vehicle wrapped
            if "Id" in data or "VehicleId" in data:
                return [data]

        return []

    def _fuzzy_match_vehicle(
        self,
        vehicles: List[Dict[str, Any]],
        search_term: str
    ) -> Optional[Dict[str, Any]]:
        """
        Fuzzy match vehicle by name/description.

        Searches in: Name, FullVehicleName, DisplayName, Description, LicencePlate
        """
        if not vehicles or not search_term:
            return None

        search_lower = search_term.lower()

        # First pass: exact match in name
        for vehicle in vehicles:
            name = (
                vehicle.get("FullVehicleName") or
                vehicle.get("Name") or
                vehicle.get("DisplayName") or
                ""
            ).lower()

            if search_lower in name:
                return vehicle

        # Second pass: partial match in any field
        for vehicle in vehicles:
            searchable = " ".join([
                str(vehicle.get("FullVehicleName", "")),
                str(vehicle.get("Name", "")),
                str(vehicle.get("DisplayName", "")),
                str(vehicle.get("Description", "")),
                str(vehicle.get("LicencePlate", "")),
                str(vehicle.get("VIN", "")),
            ]).lower()

            if search_lower in searchable:
                return vehicle

        # Third pass: word-level matching
        search_words = set(search_lower.split())
        best_match = None
        best_score = 0

        for vehicle in vehicles:
            searchable = " ".join([
                str(vehicle.get("FullVehicleName", "")),
                str(vehicle.get("Name", "")),
            ]).lower()

            vehicle_words = set(searchable.split())
            overlap = len(search_words & vehicle_words)

            if overlap > best_score:
                best_score = overlap
                best_match = vehicle

        return best_match if best_score > 0 else None
