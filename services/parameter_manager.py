"""
Parameter Manager - Type Validation, Casting, and Injection
Version: 2.0

Handles parameter resolution from multiple sources:
1. User input (FROM_USER)
2. Tool outputs (FROM_TOOL_OUTPUT)
3. Context injection (FROM_CONTEXT)

NO domain logic - purely type system operations.
"""

import json
import logging
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime, date

from services.tool_contracts import (
    UnifiedToolDefinition,
    ParameterDefinition,
    DependencySource,
    ToolExecutionContext
)
from services.patterns import get_injectable_context

logger = logging.getLogger(__name__)


class ParameterValidationError(Exception):
    """Raised when parameter validation fails."""

    def __init__(
        self,
        message: str,
        missing_params: List[str] = None,
        invalid_params: Dict[str, str] = None,
        suggested_tools: List[str] = None
    ):
        super().__init__(message)
        self.missing_params = missing_params or []
        self.invalid_params = invalid_params or {}
        self.suggested_tools = suggested_tools or []

    def to_ai_feedback(self) -> str:
        """Generate Croatian feedback for LLM."""
        parts = []

        if self.missing_params:
            parts.append(f"Nedostaju parametri: {', '.join(self.missing_params)}")

        if self.invalid_params:
            for param, reason in self.invalid_params.items():
                parts.append(f"Neispravan '{param}': {reason}")

        if self.suggested_tools:
            parts.append(
                f"Preporučeni alati za dohvat podataka: {', '.join(self.suggested_tools)}"
            )

        return ". ".join(parts) if parts else self.args[0]


class ParameterManager:
    """
    Manages parameter resolution, validation, and injection.

    Responsibilities:
    - Merge parameters from LLM, context, and tool outputs
    - Type casting and validation
    - Path parameter substitution
    - Missing parameter detection with hints

    MASTER PROMPT v9.0 - ROBUSTAN HANDOFF:
    When parameters are missing, ask for ONE parameter at a time with a clear question.
    """

    # Human-friendly parameter descriptions (Croatian)
    # Used for generating clear, single-parameter questions
    PARAM_DESCRIPTIONS: Dict[str, str] = {
        # Time/Date parameters
        "fromtime": "Od kada? (npr. 'sutra u 9:00' ili '2025-01-15T09:00')",
        "from": "Od kada? (npr. 'sutra u 9:00')",
        "totime": "Do kada? (npr. 'sutra u 17:00' ili '2025-01-15T17:00')",
        "to": "Do kada? (npr. 'sutra u 17:00')",
        "date": "Koji datum? (npr. 'sutra' ili '15.01.2025')",
        "startdate": "Početni datum?",
        "enddate": "Krajnji datum?",

        # Vehicle parameters
        "vehicleid": "Koje vozilo? (navedite registraciju ili naziv)",
        "mileage": "Kolika je trenutna kilometraža? (npr. '45000')",
        "licenceplate": "Koja je registracija vozila?",
        "vin": "Koji je VIN broj vozila?",

        # Person parameters
        "personid": "Za koju osobu?",
        "driverid": "Koji vozač?",

        # Description/Text parameters
        "description": "Možete li opisati situaciju?",
        "comment": "Imate li komentar?",
        "note": "Želite li dodati bilješku?",
        "reason": "Koji je razlog?",

        # Booking parameters
        "purpose": "Koja je svrha rezervacije?",
        "destination": "Koja je destinacija?",

        # Generic
        "name": "Koji naziv?",
        "title": "Koji naslov?",
        "value": "Koja vrijednost?",
        "amount": "Koji iznos?",
        "quantity": "Koja količina?",
        "status": "Koji status?",
        "type": "Koji tip?",
        "category": "Koja kategorija?",
    }

    def __init__(self):
        """Initialize parameter manager."""
        logger.debug("ParameterManager initialized")

    def resolve_parameters(
        self,
        tool: UnifiedToolDefinition,
        llm_params: Dict[str, Any],
        execution_context: ToolExecutionContext
    ) -> Tuple[Dict[str, Any], List[str]]:
        """
        Resolve all parameters from available sources.

        Args:
            tool: Tool definition
            llm_params: Parameters from LLM
            execution_context: Execution context with user data and tool outputs

        Returns:
            (resolved_params, warnings)

        Raises:
            ParameterValidationError: If required parameters are missing
        """
        resolved = {}
        warnings = []

        # Step 1: Inject context parameters (invisible to LLM)
        context_params = self._inject_context_params(
            tool,
            execution_context.user_context
        )
        resolved.update(context_params)

        # Step 2: Resolve FROM_TOOL_OUTPUT dependencies
        output_params, output_warnings = self._resolve_output_params(
            tool,
            execution_context.tool_outputs
        )
        resolved.update(output_params)
        warnings.extend(output_warnings)

        # Step 3: Add LLM-provided parameters
        user_params, user_warnings = self._process_user_params(
            tool,
            llm_params
        )
        resolved.update(user_params)
        warnings.extend(user_warnings)

        # Step 4: Validate and cast types
        validated, cast_warnings = self._validate_and_cast(tool, resolved)
        warnings.extend(cast_warnings)

        # Step 5: Check required parameters
        # MASTER PROMPT v9.0 - ROBUSTAN HANDOFF: Ask for ONE parameter at a time
        missing = self._check_required_params(tool, validated)
        if missing:
            # Get the FIRST missing parameter only
            first_missing = missing[0]

            # Generate human-friendly question for this specific parameter
            question = self._get_parameter_question(first_missing, tool)

            # Find tools that can provide missing params (for AI context)
            suggested = self._suggest_provider_tools(tool, missing)

            raise ParameterValidationError(
                question,  # Single, clear question
                missing_params=[first_missing],  # Only first parameter
                suggested_tools=suggested
            )

        logger.debug(
            f"Resolved {len(validated)} params for {tool.operation_id}"
        )

        return validated, warnings

    def _inject_context_params(
        self,
        tool: UnifiedToolDefinition,
        user_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Inject parameters from context (invisible to LLM).

        FIX #15: Supports deep injection into nested objects.

        Example:
            Tool has parameter: filter (type: object)
            user_context = {"tenant_id": "abc123", "person_id": "user_456"}

            Result: {"filter": {"tenant_id": "abc123", "person_id": "user_456"}}
        """
        injected = {}

        # FIX v13.3: Skip certain params that have incorrect context_key in Swagger metadata
        # VehicleId should come from user context vehicle.id, not person_id
        skip_injection = set()
        if tool.operation_id == "post_VehicleCalendar":
            skip_injection = {"VehicleId", "EntryType", "AssigneeType"}
        elif tool.operation_id == "post_AddMileage":
            skip_injection = {"VehicleId"}  # VehicleId comes from user_context.vehicle.id

        # STEP 1: Direct context parameter injection (existing behavior)
        for param_name, param_def in tool.get_context_params().items():
            if param_name in skip_injection:
                continue

            context_key = param_def.context_key or param_name.lower()

            if context_key in user_context:
                value = user_context[context_key]
                if value is not None:
                    injected[param_name] = value
                    logger.debug(f"Injected context param: {param_name}")

        # STEP 2: Deep injection for nested object parameters (FIX #15)
        # Find ALL parameters with type="object" that are FROM_USER
        for param_name, param_def in tool.parameters.items():
            # Skip if already injected
            if param_name in injected:
                continue

            # Only handle object types
            if param_def.param_type != "object":
                continue

            # Only process FROM_USER params (not FROM_CONTEXT - those are handled above)
            if param_def.dependency_source != DependencySource.FROM_USER:
                continue

            # Check if this object parameter should have nested context fields
            # Common patterns: "filter", "filters", "query", "criteria"
            nested_object = self._build_nested_context_object(
                param_name,
                user_context
            )

            if nested_object:
                injected[param_name] = nested_object
                logger.debug(
                    f"Deep injection: {param_name} with {len(nested_object)} fields"
                )

        return injected

    def _build_nested_context_object(
        self,
        param_name: str,
        user_context: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Build nested object with context fields (FIX #15).

        Injects tenant_id and person_id into nested objects if they exist
        in user_context.

        Args:
            param_name: Name of the object parameter (e.g., "filter")
            user_context: User context dict

        Returns:
            Dict with injected fields, or None if no fields to inject
        """
        # Use centralized injectable context from patterns.py
        # This normalizes all context keys and extracts person_id/tenant_id
        nested_obj = get_injectable_context(user_context)

        for key, value in nested_obj.items():
            logger.debug(f"  -> Injected {key}={value} into {param_name}")

        # Return None if no fields were injected
        return nested_obj if nested_obj else None

    def _resolve_output_params(
        self,
        tool: UnifiedToolDefinition,
        tool_outputs: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], List[str]]:
        """Resolve parameters from previous tool outputs."""
        resolved = {}
        warnings = []

        for param_name, param_def in tool.get_output_params().items():
            # Search in tool_outputs for matching key
            found = False

            for output_data in tool_outputs.values():
                if not isinstance(output_data, dict):
                    continue

                # Try direct key match
                if param_name in output_data:
                    resolved[param_name] = output_data[param_name]
                    found = True
                    break

                # Try case-insensitive match
                for key, value in output_data.items():
                    if key.lower() == param_name.lower():
                        resolved[param_name] = value
                        found = True
                        break

                if found:
                    break

            if not found:
                warnings.append(
                    f"Could not resolve {param_name} from tool outputs"
                )

        return resolved, warnings

    def _process_user_params(
        self,
        tool: UnifiedToolDefinition,
        llm_params: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], List[str]]:
        """
        Process parameters provided by LLM.

        ANTI-HALLUCINATION PROTOCOL:
        - Detect fake/example values (e.g., example.com, test@test.com)
        - Reject placeholder patterns
        - Force LLM to ask user instead of guessing
        """
        processed = {}
        warnings = []

        user_param_defs = tool.get_user_params()

        for param_name, value in llm_params.items():
            if value is None:
                continue

            # ANTI-HALLUCINATION CHECK
            if self._is_hallucinated_value(param_name, value):
                logger.warning(
                    f"🚨 HALLUCINATION DETECTED: {param_name}={value}"
                )
                raise ParameterValidationError(
                    f"Parametar '{param_name}' izgleda kao izmišljena vrijednost. "
                    f"Zatraži stvarnu vrijednost od korisnika.",
                    missing_params=[param_name]
                )

            # Check if this is a valid user parameter
            if param_name not in user_param_defs:
                # Try case-insensitive match
                matched = False
                for def_name in user_param_defs.keys():
                    if def_name.lower() == param_name.lower():
                        processed[def_name] = value
                        matched = True
                        break

                if not matched:
                    # FIX v13.3: Special handling for VehicleCalendar booking params
                    # These params come from flow_handler, not LLM, so pass them through
                    if tool.operation_id == "post_VehicleCalendar" and param_name in {
                        "VehicleId", "AssignedToId", "FromTime", "ToTime",
                        "EntryType", "AssigneeType", "Description"
                    }:
                        processed[param_name] = value
                        logger.debug(f"Passed through booking param: {param_name}")
                        continue

                    # FIX v13.4: Special handling for AddMileage params
                    # VehicleId comes from user_context.vehicle.id, not context injection
                    if tool.operation_id == "post_AddMileage" and param_name in {
                        "VehicleId", "Value", "Comment", "Time"
                    }:
                        processed[param_name] = value
                        logger.debug(f"Passed through mileage param: {param_name}")
                        continue

                    # FIX v13.5: Special handling for AddCase params
                    # These come from flow_handler, not standard param resolution
                    if tool.operation_id == "post_AddCase" and param_name in {
                        "User", "Subject", "Message"
                    }:
                        processed[param_name] = value
                        logger.debug(f"Passed through case param: {param_name}")
                        continue

                    # FIX v13.2: Log at debug level, not warning, because
                    # some params like personId are intentionally added by
                    # tool_executor AFTER this processing step
                    logger.debug(
                        f"Parameter '{param_name}' not in Swagger definition - "
                        f"will be handled by executor if needed"
                    )
                    continue
            else:
                processed[param_name] = value

        return processed, warnings

    def _is_hallucinated_value(self, param_name: str, value: Any) -> bool:
        """
        Detect if a value is OBVIOUSLY hallucinated/fake.

        CRITICAL: This should be CONSERVATIVE - only block obvious fakes.
        We don't want to block legitimate data.

        Args:
            param_name: Parameter name
            value: Value to check

        Returns:
            True if value looks OBVIOUSLY fake
        """
        if not isinstance(value, str):
            return False

        value_lower = value.lower().strip()

        # ONLY block OBVIOUS email hallucinations (exact matches only)
        if "email" in param_name.lower():
            # Only block if it's an EXACT match to common placeholders
            obvious_fakes = [
                "example@example.com",
                "test@test.com",
                "user@example.com",
                "admin@example.com",
                "demo@demo.com",
                "sample@sample.com",
                "test@localhost"
            ]

            if value_lower in obvious_fakes:
                return True

        # UUID hallucinations - only block all-zeros
        if "id" in param_name.lower() or "uuid" in param_name.lower():
            # Only block all zeros
            cleaned = value_lower.replace("-", "").replace("0", "")
            if len(cleaned) == 0 and len(value) > 10:
                return True

        # Only block EXACT placeholder text (not substrings)
        exact_placeholders = [
            "lorem ipsum",
            "placeholder",
            "test data",
            "dummy data"
        ]

        if value_lower in exact_placeholders:
            return True

        return False

    def _validate_and_cast(
        self,
        tool: UnifiedToolDefinition,
        params: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], List[str]]:
        """Validate and cast parameter types."""
        validated = {}
        warnings = []

        for param_name, value in params.items():
            if value is None:
                continue

            param_def = tool.parameters.get(param_name)
            if not param_def:
                # Parameter not in schema - pass through
                validated[param_name] = value
                continue

            try:
                casted = self._cast_type(
                    value,
                    param_def.param_type,
                    param_def.format
                )
                validated[param_name] = casted
            except (ValueError, TypeError) as e:
                warnings.append(
                    f"Type casting failed for {param_name}: {e}"
                )
                validated[param_name] = value  # Keep original

        return validated, warnings

    def _cast_type(
        self,
        value: Any,
        expected_type: str,
        param_format: Optional[str] = None
    ) -> Any:
        """Cast value to expected type."""
        if value is None:
            return None

        # Integer
        if expected_type == "integer":
            if isinstance(value, int):
                return value
            if isinstance(value, str):
                # Handle "100.0" -> 100
                return int(float(value))
            return int(value)

        # Number (float)
        if expected_type == "number":
            if isinstance(value, (int, float)):
                return float(value)
            return float(value)

        # Boolean
        if expected_type == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes", "da")
            return bool(value)

        # String
        if expected_type == "string":
            if param_format == "date-time":
                return self._parse_datetime(value)
            if param_format == "date":
                return self._parse_date(value)
            return str(value)

        # Array
        if expected_type == "array":
            if isinstance(value, list):
                return value
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    return [value]
            return [value]

        # Object
        if expected_type == "object":
            if isinstance(value, dict):
                return value
            if isinstance(value, str):
                return json.loads(value)
            return value

        return value

    def _parse_datetime(self, value: Any) -> str:
        """Parse datetime to ISO 8601 format."""
        if isinstance(value, datetime):
            return value.isoformat()

        if isinstance(value, str):
            # Already ISO format
            if "T" in value and len(value) >= 19:
                return value

            # Try common formats
            formats = [
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%d.%m.%Y %H:%M",
                "%Y-%m-%d"
            ]

            for fmt in formats:
                try:
                    dt = datetime.strptime(value, fmt)
                    return dt.isoformat()
                except ValueError:
                    continue

        return str(value)

    def _parse_date(self, value: Any) -> str:
        """Parse date to YYYY-MM-DD format."""
        if isinstance(value, date):
            return value.strftime("%Y-%m-%d")

        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d")

        if isinstance(value, str):
            # Already correct format
            if len(value) == 10 and value.count("-") == 2:
                return value

            # Try formats
            formats = ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"]
            for fmt in formats:
                try:
                    dt = datetime.strptime(value, fmt)
                    return dt.strftime("%Y-%m-%d")
                except ValueError:
                    continue

        return str(value)

    def _check_required_params(
        self,
        tool: UnifiedToolDefinition,
        params: Dict[str, Any]
    ) -> List[str]:
        """Check for missing required parameters."""
        missing = []

        # FIX v13.3: Skip params with incorrect context_key/dependency_source
        # These have incorrect metadata in Swagger definitions
        # - VehicleId comes from user selection or user_context.vehicle.id, not person_id
        # - EntryType/AssigneeType will be injected by executor
        skip_params = set()
        if tool.operation_id == "post_VehicleCalendar":
            skip_params = {"VehicleId", "EntryType", "AssigneeType"}
        elif tool.operation_id == "post_AddMileage":
            # VehicleId comes from llm_params (passed from flow/executor), not context
            skip_params = {"VehicleId"}
        elif tool.operation_id == "post_AddCase":
            # All params come from flow, not context injection
            skip_params = {"User", "Subject", "Message"}

        for param_name in tool.required_params:
            if param_name in skip_params:
                continue
            if param_name not in params or params[param_name] is None:
                missing.append(param_name)

        return missing

    def _suggest_provider_tools(
        self,
        tool: UnifiedToolDefinition,
        missing_params: List[str]
    ) -> List[str]:
        """
        Suggest tools that can provide missing parameters.

        POPRAVAK: Implementacija umjesto placeholder-a.

        Koristi semantičko mapiranje za pronalazak provider alata.
        Ne ovisi o registry-ju jer ParameterManager nema pristup.

        Args:
            tool: Tool that needs parameters
            missing_params: List of missing parameter names

        Returns:
            List of suggested provider tool patterns (za AI feedback)
        """
        suggestions = []

        # Mapping: parameter type → suggested tool patterns
        PROVIDER_PATTERNS: Dict[str, List[str]] = {
            # Vehicle-related
            'vehicleid': ['get_Vehicles', 'get_MasterData', 'get_AvailableVehicles'],
            'vehicle': ['get_Vehicles', 'get_MasterData'],
            'licenceplate': ['get_Vehicles', 'get_MasterData'],

            # Person-related
            'personid': ['get_Persons', 'get_Drivers', 'get_Users'],
            'driverid': ['get_Drivers', 'get_Persons'],
            'userid': ['get_Users', 'get_Persons'],

            # Booking-related
            'bookingid': ['get_VehicleCalendar', 'get_Bookings'],
            'reservationid': ['get_VehicleCalendar', 'get_Reservations'],

            # Location-related
            'locationid': ['get_Locations', 'get_Sites'],
            'siteid': ['get_Sites', 'get_Locations'],

            # Case-related
            'caseid': ['get_Cases', 'get_ServiceCases'],
        }

        for param in missing_params:
            param_lower = param.lower()

            # Direct match
            if param_lower in PROVIDER_PATTERNS:
                suggestions.extend(PROVIDER_PATTERNS[param_lower])
                continue

            # Partial match (e.g., "VehicleId" matches "vehicleid")
            for pattern_key, providers in PROVIDER_PATTERNS.items():
                if pattern_key in param_lower or param_lower in pattern_key:
                    suggestions.extend(providers)
                    break

        # Remove duplicates while preserving order
        seen = set()
        unique_suggestions = []
        for s in suggestions:
            if s not in seen:
                seen.add(s)
                unique_suggestions.append(s)

        return unique_suggestions[:5]  # Max 5 suggestions

    def _get_parameter_question(
        self,
        param_name: str,
        tool: UnifiedToolDefinition
    ) -> str:
        """
        Generate human-friendly question for a missing parameter.

        MASTER PROMPT v9.0 - ROBUSTAN HANDOFF:
        Returns a SINGLE, CLEAR question in Croatian for the user.

        Priority:
        1. Check PARAM_DESCRIPTIONS map (most common params)
        2. Use parameter's own description from Swagger
        3. Generate generic question from param name

        Args:
            param_name: Name of missing parameter
            tool: Tool definition (for accessing param description)

        Returns:
            Human-friendly question in Croatian
        """
        param_lower = param_name.lower()

        # Priority 1: Check our predefined descriptions
        if param_lower in self.PARAM_DESCRIPTIONS:
            return self.PARAM_DESCRIPTIONS[param_lower]

        # Priority 2: Use parameter's Swagger description if available
        param_def = tool.parameters.get(param_name)
        if param_def and param_def.description:
            desc = param_def.description.strip()
            # If description is a question or statement, use it
            if desc:
                # Add question mark if missing
                if not desc.endswith("?"):
                    return f"{desc}?"
                return desc

        # Priority 3: Generate question from param name
        # Convert camelCase/PascalCase to readable format
        import re
        readable = re.sub(r'([a-z])([A-Z])', r'\1 \2', param_name)
        readable = readable.replace("_", " ").lower()

        return f"Molim unesite {readable}:"

    def prepare_request(
        self,
        tool: UnifiedToolDefinition,
        params: Dict[str, Any]
    ) -> Tuple[str, Optional[Dict], Optional[Dict]]:
        """
        Prepare HTTP request components.

        Args:
            tool: Tool definition
            params: Validated parameters

        Returns:
            (path, query_params, body)
        """
        query_params = {}
        body_params = {}
        path = tool.path

        for param_name, value in params.items():
            if value is None:
                continue

            param_def = tool.parameters.get(param_name)
            if not param_def:
                # Unknown param - add to body by default
                body_params[param_name] = value
                continue

            location = param_def.location

            if location == "path":
                # Substitute in path template
                path = path.replace(f"{{{param_name}}}", str(value))
                path = path.replace(f"{{{{{param_name}}}}}", str(value))
            elif location == "query":
                query_params[param_name] = value
            elif location == "header":
                # Headers handled separately in executor
                pass
            else:  # body
                body_params[param_name] = value

        # For GET/DELETE: all params go to query
        if tool.method in ("GET", "DELETE"):
            return (
                path,
                params if params else None,
                None
            )

        # For POST/PUT/PATCH: separate query and body
        return (
            path,
            query_params if query_params else None,
            body_params if body_params else None
        )
