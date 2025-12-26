"""
Flow Handler - Multi-turn conversation flows.
Version: 1.0

Single responsibility: Handle selection, confirmation, gathering, and availability flows.
"""

import logging
from typing import Dict, Any, List, Optional

from services.booking_contracts import AssigneeType, EntryType
from services.error_translator import get_error_translator

logger = logging.getLogger(__name__)


class FlowHandler:
    """
    Handles multi-turn conversation flows.

    Responsibilities:
    - Handle item selection
    - Handle confirmations
    - Handle parameter gathering
    - Handle availability checks
    """

    def __init__(self, registry, executor, ai, formatter):
        """Initialize flow handler."""
        self.registry = registry
        self.executor = executor
        self.ai = ai
        self.formatter = formatter

    async def handle_availability(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        user_context: Dict[str, Any],
        conv_manager
    ) -> Dict[str, Any]:
        """
        Handle vehicle availability check.

        Returns vehicles found and sets up confirmation flow.
        """
        tool = self.registry.get_tool(tool_name)
        if not tool:
            return {
                "success": False,
                "error": f"Tool {tool_name} not found",
                "final_response": f"Tehnicki problem - alat '{tool_name}' nije pronaden."
            }

        from services.tool_contracts import ToolExecutionContext
        execution_context = ToolExecutionContext(
            user_context=user_context,
            tool_outputs={},
            conversation_state={}
        )

        result = await self.executor.execute(tool, parameters, execution_context)

        if not result.success:
            return {
                "success": False,
                "data": {"error": result.error_message},
                "ai_feedback": result.ai_feedback or "Availability check failed",
                "final_response": f"Greska: {result.error_message}"
            }

        # Extract items
        items = self._extract_items(result.data)

        if not items:
            return {
                "success": True,
                "data": result.data,
                "final_response": (
                    "Nazalost, nema slobodnih vozila za odabrani period.\n\n"
                    "Mozete li odabrati drugi termin? Na primjer:\n"
                    "* 'Sutra od 8 do 17'\n"
                    "* 'Sljedeci tjedan od ponedjeljka do srijede'"
                )
            }

        # Show first vehicle
        first_vehicle = items[0]

        vehicle_name = (
            first_vehicle.get("FullVehicleName") or
            first_vehicle.get("DisplayName") or
            first_vehicle.get("Name") or
            "Vozilo"
        )
        plate = (
            first_vehicle.get("LicencePlate") or
            first_vehicle.get("Plate") or
            first_vehicle.get("RegistrationNumber") or
            "N/A"
        )
        vehicle_id = first_vehicle.get("Id") or first_vehicle.get("VehicleId")

        # Store for confirmation
        await conv_manager.set_displayed_items(items)
        await conv_manager.add_parameters(parameters)
        await conv_manager.select_item(first_vehicle)

        if hasattr(conv_manager.context, 'tool_outputs'):
            conv_manager.context.tool_outputs["VehicleId"] = vehicle_id
            conv_manager.context.tool_outputs["vehicleId"] = vehicle_id

        conv_manager.context.current_tool = "post_VehicleCalendar"

        from_time = parameters.get("from") or parameters.get("FromTime", "N/A")
        to_time = parameters.get("to") or parameters.get("ToTime", "N/A")

        message = (
            f"**Pronasao sam slobodno vozilo:**\n\n"
            f"**{vehicle_name}** ({plate})\n\n"
            f"Period: {from_time} -> {to_time}\n\n"
        )

        if len(items) > 1:
            message += f"_(Ima jos {len(items) - 1} slobodnih vozila)_\n\n"

        message += "**Zelite li potvrditi rezervaciju?** (Da/Ne)"

        await conv_manager.request_confirmation(message)
        await conv_manager.save()

        return {
            "success": True,
            "data": result.data,
            "needs_input": True,
            "prompt": message
        }

    async def request_confirmation(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        user_context: Dict[str, Any],
        conv_manager
    ) -> Dict[str, Any]:
        """Request confirmation for critical operation."""
        await conv_manager.add_parameters(parameters)

        tool = self.registry.get_tool(tool_name)
        desc = tool.description[:100] if tool and tool.description else tool_name

        message = f"**Potvrda operacije:**\n\n{desc}\n\n"

        for key, value in list(parameters.items())[:5]:
            if value is not None:
                message += f"* {key}: {value}\n"

        message += "\n_Potvrdite s 'Da' ili odustanite s 'Ne'._"

        await conv_manager.request_confirmation(message)
        conv_manager.context.current_tool = tool_name
        await conv_manager.save()

        return {
            "success": True,
            "data": {},
            "needs_input": True,
            "prompt": message
        }

    async def handle_selection(
        self,
        sender: str,
        text: str,
        user_context: Dict[str, Any],
        conv_manager,
        handle_new_request_fn
    ) -> str:
        """Handle item selection."""
        selected = conv_manager.parse_item_selection(text)

        if not selected:
            return "Nisam razumio odabir.\nMolimo navedite broj (npr. '1') ili naziv."

        await conv_manager.select_item(selected)

        params = conv_manager.get_parameters()

        vehicle_name = (
            selected.get("FullVehicleName") or
            selected.get("DisplayName") or
            selected.get("Name") or
            "Vozilo"
        )
        plate = selected.get("LicencePlate") or selected.get("Plate", "N/A")

        from_time = params.get("from") or params.get("FromTime", "N/A")
        to_time = params.get("to") or params.get("ToTime", "N/A")

        message = (
            f"**Potvrdite rezervaciju:**\n\n"
            f"Vozilo: {vehicle_name} ({plate})\n"
            f"Od: {from_time}\n"
            f"Do: {to_time}\n\n"
            f"_Potvrdite s 'Da' ili odustanite s 'Ne'._"
        )

        await conv_manager.request_confirmation(message)
        conv_manager.context.current_tool = "post_VehicleCalendar"
        await conv_manager.save()

        return message

    async def handle_confirmation(
        self,
        sender: str,
        text: str,
        user_context: Dict[str, Any],
        conv_manager
    ) -> str:
        """Handle confirmation response."""
        confirmation = conv_manager.parse_confirmation(text)

        if confirmation is None:
            return "Molim potvrdite s 'Da' ili odustanite s 'Ne'."

        if not confirmation:
            await conv_manager.cancel()
            return "Rezervacija otkazana. Kako vam jos mogu pomoci?"

        await conv_manager.confirm()

        tool_name = conv_manager.get_current_tool()
        params = conv_manager.get_parameters()
        selected = conv_manager.get_selected_item()

        # Build booking payload
        if tool_name == "post_VehicleCalendar":
            vehicle_id = None
            if selected:
                vehicle_id = selected.get("Id") or selected.get("VehicleId")
            if not vehicle_id and hasattr(conv_manager.context, 'tool_outputs'):
                vehicle_id = conv_manager.context.tool_outputs.get("VehicleId")

            if not vehicle_id:
                await conv_manager.reset()
                return "Greska: Nije odabrano vozilo. Pokusajte ponovno."

            from_time = params.get("from") or params.get("FromTime")
            to_time = params.get("to") or params.get("ToTime")

            if not from_time or not to_time:
                await conv_manager.reset()
                return "Greska: Nedostaje vrijeme rezervacije. Pokusajte ponovno."

            params = {
                "AssignedToId": user_context.get("person_id"),
                "VehicleId": vehicle_id,
                "FromTime": from_time,
                "ToTime": to_time,
                "AssigneeType": AssigneeType.PERSON,
                "EntryType": EntryType.BOOKING,
                "Description": params.get("Description") or params.get("description")
            }
        else:
            if selected:
                vehicle_id = selected.get("Id") or selected.get("VehicleId")
                if vehicle_id:
                    params["VehicleId"] = vehicle_id

            if "from" in params and "FromTime" not in params:
                params["FromTime"] = params.pop("from")
            if "to" in params and "ToTime" not in params:
                params["ToTime"] = params.pop("to")

        # Execute
        tool = self.registry.get_tool(tool_name)
        if not tool:
            await conv_manager.reset()
            return f"Tehnicki problem - alat '{tool_name}' nije pronaden."

        from services.tool_contracts import ToolExecutionContext
        execution_context = ToolExecutionContext(
            user_context=user_context,
            tool_outputs=(
                conv_manager.context.tool_outputs
                if hasattr(conv_manager.context, 'tool_outputs')
                else {}
            ),
            conversation_state={}
        )

        result = await self.executor.execute(tool, params, execution_context)

        await conv_manager.complete()
        await conv_manager.reset()

        if result.success:
            if tool_name == "post_VehicleCalendar":
                vehicle_name = ""
                if selected:
                    vehicle_name = (
                        selected.get("FullVehicleName") or
                        selected.get("DisplayName") or
                        selected.get("Name") or
                        ""
                    )
                    plate = selected.get("LicencePlate") or selected.get("Plate") or ""
                    if plate:
                        vehicle_name = f"{vehicle_name} ({plate})"

                return (
                    f"**Rezervacija uspjesna!**\n\n"
                    f"Vozilo: {vehicle_name}\n"
                    f"Period: {params.get('FromTime')} -> {params.get('ToTime')}\n\n"
                    f"Sretno na putu!"
                )

            created_id = ""
            if isinstance(result.data, dict):
                created_id = result.data.get("created_id", "") or result.data.get("Id", "")

            return (
                f"**Operacija uspjesna!**\n\n"
                f"{'ID: ' + str(created_id) if created_id else ''}\n\n"
                f"Kako vam jos mogu pomoci?"
            )
        else:
            error = result.error_message or "Nepoznata greska"
            translator = get_error_translator()
            return translator.get_user_message(error, tool_name)

    # Semantički opisi parametara za AI extraction
    PARAM_DESCRIPTIONS = {
        # Mileage parameters
        "Value": "Kilometraža vozila u km (npr. 14000, 50000)",
        "mileage": "Kilometraža vozila u km",
        "kilometraža": "Udaljenost u kilometrima",
        "Mileage": "Trenutna kilometraža vozila",
        # Vehicle parameters
        "VehicleId": "ID vozila (UUID format)",
        "vehicleId": "ID vozila",
        # Time parameters
        "from": "Početno vrijeme (datum i sat, npr. 'sutra u 9:00')",
        "to": "Završno vrijeme (datum i sat, npr. 'sutra u 17:00')",
        "FromTime": "Početno vrijeme rezervacije",
        "ToTime": "Završno vrijeme rezervacije",
        # Other parameters
        "Description": "Opis situacije ili napomena",
        "description": "Tekstualni opis",
    }

    async def handle_gathering(
        self,
        sender: str,
        text: str,
        user_context: Dict[str, Any],
        conv_manager,
        handle_new_request_fn
    ) -> str:
        """Handle parameter gathering."""
        missing = conv_manager.get_missing_params()

        # Dodaj semantički kontekst za svaki parametar
        extracted = await self.ai.extract_parameters(
            text,
            [{"name": p, "type": "string", "description": self.PARAM_DESCRIPTIONS.get(p, p)} for p in missing]
        )

        await conv_manager.add_parameters(extracted)

        if conv_manager.has_all_required_params():
            return await handle_new_request_fn(sender, text, user_context, conv_manager)

        still_missing = conv_manager.get_missing_params()
        return self._build_param_prompt(still_missing)

    def _extract_items(self, data: Any) -> List[Dict]:
        """Extract items from API response."""
        items = []
        if isinstance(data, dict):
            items = data.get("items", [])
            if not items:
                items = data.get("Data", [])
            if not items and "data" in data:
                nested = data["data"]
                if isinstance(nested, dict):
                    items = nested.get("Data", [])
                elif isinstance(nested, list):
                    items = nested
        return items

    def _build_param_prompt(self, missing: List[str]) -> str:
        """Build prompt for missing parameters."""
        prompts = {
            # Time parameters
            "from": "Od kada vam treba? (npr. 'sutra u 9:00')",
            "to": "Do kada? (npr. 'sutra u 17:00')",
            "FromTime": "Od kada vam treba?",
            "ToTime": "Do kada?",
            # Description
            "Description": "Možete li opisati situaciju?",
            "description": "Opišite situaciju",
            # Vehicle
            "VehicleId": "Koje vozilo želite?",
            "vehicleId": "Koje vozilo?",
            # Mileage parameters
            "Value": "Kolika je kilometraža? (npr. '14000 km')",
            "mileage": "Kolika je trenutna kilometraža?",
            "Mileage": "Unesite kilometražu vozila",
            "kilometraža": "Koliko kilometara ima vozilo?",
        }

        if len(missing) == 1:
            return prompts.get(missing[0], f"Trebam jos: {missing[0]}")

        lines = ["Za nastavak trebam jos informacije:"]
        for param in missing[:3]:
            lines.append(f"* {prompts.get(param, param)}")

        return "\n".join(lines)
