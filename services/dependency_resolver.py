"""
Dependency Resolver - Automatic Tool Chaining Engine
Version: 1.0

KRITIČNA KOMPONENTA za 10/10 ocjenu sustava.

Rješava probleme:
1. Korisnik ne zna UUID-ove (VehicleId, PersonId, etc.)
2. Korisnik daje human-readable vrijednosti (registracija, ime)
3. Sustav mora automatski resolvati dependencies

Primjer:
    Korisnik: "Rezerviraj vozilo ZG-1234-AB za sutra"

    PRIJE (broken):
        Bot: "Koje vozilo? (unesite ID)" ← BESMISLENO!

    POSLIJE (fixed):
        1. Detektiraj da treba VehicleId
        2. Prepoznaj "ZG-1234-AB" kao registraciju
        3. Automatski pozovi get_Vehicles(Filter="LicencePlate=ZG-1234-AB")
        4. Izvuci VehicleId iz rezultata
        5. Nastavi s originalnim alatom

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


class DependencyResolver:
    """
    Resolves parameter dependencies automatically.

    Features:
    - Pattern recognition for human-readable values
    - Automatic provider tool selection
    - Filter query construction
    - Caching of resolved values

    KRITIČNO: Ovo omogućava da korisnik kaže "ZG-1234-AB"
              umjesto UUID-a!
    """

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
