"""
Response Formatter
Version: 10.0

Formats API responses for WhatsApp.
NO DEPENDENCIES on other services.
"""

import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class ResponseFormatter:
    """
    Formats API responses for user display.

    Features:
    - Dynamic formatting based on data type
    - Croatian language
    - Emoji support
    - List formatting
    - FIX v13.2: Message length limits for WhatsApp
    """

    # FIX v13.2: WhatsApp message limits
    MAX_MESSAGE_LENGTH = 3500  # Leave buffer for emojis/markdown
    MAX_LIST_ITEMS = 5  # Reduced from 10 to prevent flooding
    MAX_FIELD_VALUE_LENGTH = 80  # Truncate long field values

    def _truncate_message(self, message: str) -> str:
        """
        FIX v13.2: Truncate message to WhatsApp limit.

        Ensures messages don't exceed MAX_MESSAGE_LENGTH.
        """
        if len(message) <= self.MAX_MESSAGE_LENGTH:
            return message

        # Find last complete line before limit
        truncated = message[:self.MAX_MESSAGE_LENGTH]
        last_newline = truncated.rfind('\n')

        if last_newline > self.MAX_MESSAGE_LENGTH - 500:
            truncated = truncated[:last_newline]

        return truncated + "\n\n_...poruka skraćena._"

    def format_result(
        self,
        result: Dict[str, Any],
        tool: Optional[Any] = None  # Can be UnifiedToolDefinition (Pydantic) or dict
    ) -> str:
        """
        Format API result for display.

        Args:
            result: Execution result
            tool: Tool metadata (UnifiedToolDefinition or dict)

        Returns:
            Formatted string
        """
        if not result.get("success"):
            error = result.get("error", "Nepoznata greška")
            return f"❌ Greška: {error}"

        operation = result.get("operation", "")
        # CRITICAL FIX: Handle both Pydantic (UnifiedToolDefinition) and dict
        if tool:
            method = tool.method if hasattr(tool, 'method') else tool.get("method", "GET")
        else:
            method = "GET"
        
        if method == "GET":
            return self._format_get(result, operation)
        elif method in ("POST", "PUT", "PATCH"):
            return self._format_mutation(result, operation)
        elif method == "DELETE":
            return "✅ Uspješno obrisano."
        
        return "✅ Operacija uspješna."
    
    def _format_get(self, result: Dict, operation: str) -> str:
        """Format GET response."""
        if "items" in result:
            items = result["items"]
            count = result.get("count", len(items))

            if not items:
                return "Nema pronađenih rezultata."

            if self._is_vehicle(items[0] if items else {}):
                return self.format_vehicle_list(items)
            elif self._is_person(items[0] if items else {}):
                return self._format_person_list(items)
            else:
                return self._format_generic_list(items, count)

        if "data" in result:
            data = result["data"]

            # CRITICAL FIX v12.2: Unwrap nested "Data" field from API responses
            # API often returns: {"Data": [...], "Count": 10} inside result["data"]
            if isinstance(data, dict) and "Data" in data:
                nested_data = data["Data"]
                count = data.get("Count", len(nested_data) if isinstance(nested_data, list) else 1)

                if isinstance(nested_data, list):
                    if not nested_data:
                        return "Nema pronađenih rezultata."
                    if self._is_vehicle(nested_data[0]):
                        return self.format_vehicle_list(nested_data)
                    elif self._is_person(nested_data[0]):
                        return self._format_person_list(nested_data)
                    elif self._is_masterdata(nested_data[0]):
                        return self._format_masterdata(nested_data[0])
                    return self._format_generic_list(nested_data, count)
                elif isinstance(nested_data, dict):
                    if self._is_masterdata(nested_data):
                        return self._format_masterdata(nested_data)
                    elif self._is_vehicle(nested_data):
                        return self._format_vehicle_details(nested_data)
                    return self._format_generic_object(nested_data)

            # CRITICAL FIX: Type guard - only check structure on dict/list, not primitives
            if isinstance(data, dict):
                if self._is_vehicle(data):
                    return self._format_vehicle_details(data)
                elif self._is_masterdata(data):
                    return self._format_masterdata(data)
                else:
                    return self._format_generic_object(data)
            elif isinstance(data, list):
                # List of items without "items" wrapper
                if not data:
                    return "Nema pronađenih rezultata."
                if isinstance(data[0], dict):
                    if self._is_vehicle(data[0]):
                        return self.format_vehicle_list(data)
                    elif self._is_person(data[0]):
                        return self._format_person_list(data)
                    elif self._is_masterdata(data[0]):
                        return self._format_masterdata(data[0])
                return self._format_generic_list(data, len(data))
            else:
                # Primitive type (string, number, etc.)
                return f"✅ Rezultat: {data}"

        return "✅ Operacija uspješna."
    
    def _format_mutation(self, result: Dict, operation: str) -> str:
        """Format POST/PUT/PATCH response."""
        created_id = result.get("created_id")
        operation_lower = operation.lower()
        
        if "calendar" in operation_lower or "booking" in operation_lower:
            msg = f"✅ **Rezervacija uspješna!**"
            if created_id:
                msg += f"\n\n📝 ID: {created_id}"
            return msg
        
        if "case" in operation_lower:
            msg = f"✅ **Prijava zaprimljena!**"
            if created_id:
                msg += f"\n\n📝 ID: {created_id}"
            msg += "\n\nNaš tim će vas kontaktirati uskoro."
            return msg
        
        if "mileage" in operation_lower:
            return "✅ **Kilometraža zabilježena!**"
        
        if "email" in operation_lower:
            return "✅ **Email poslan!**"
        
        msg = f"✅ Operacija uspješna!"
        if created_id:
            msg += f"\n📝 ID: {created_id}"
        
        return msg
    
    def format_vehicle_list(self, vehicles: List[Dict]) -> str:
        """Format vehicle list for selection."""
        if not vehicles:
            return "Nema dostupnih vozila."
        
        lines = [f"🚗 **Pronađeno {len(vehicles)} vozila:**\n"]
        
        for i, v in enumerate(vehicles[:10], 1):
            name = (
                v.get("FullVehicleName") or
                v.get("DisplayName") or
                v.get("Name") or
                f"{v.get('Manufacturer', '')} {v.get('Model', '')}".strip() or
                "Vozilo"
            )
            plate = v.get("LicencePlate") or v.get("Plate") or "N/A"
            
            lines.append(f"**{i}.** {name}")
            lines.append(f"   📋 Registracija: {plate}")
            lines.append("")
        
        lines.append("_Odaberite vozilo brojem (npr. '1') ili imenom._")
        
        return "\n".join(lines)
    
    def _format_person_list(self, persons: List[Dict]) -> str:
        """Format person list."""
        lines = [f"👥 **Pronađeno {len(persons)} osoba:**\n"]
        
        for i, p in enumerate(persons[:10], 1):
            name = p.get("DisplayName") or p.get("Name") or "N/A"
            phone = p.get("Phone") or p.get("Mobile") or ""
            
            lines.append(f"{i}. {name}")
            if phone:
                lines.append(f"   📱 {phone}")
        
        return "\n".join(lines)
    
    def _format_generic_list(self, items: List[Dict], count: int) -> str:
        """Format generic list."""
        lines = [f"📋 **Pronađeno {count} stavki:**\n"]
        
        for i, item in enumerate(items[:10], 1):
            name = (
                item.get("Name") or
                item.get("Title") or
                item.get("DisplayName") or
                item.get("Description") or
                f"Stavka {i}"
            )
            lines.append(f"{i}. {name}")
        
        if count > 10:
            lines.append(f"\n_...i još {count - 10} stavki_")
        
        return "\n".join(lines)
    
    def _format_vehicle_details(self, data: Dict) -> str:
        """Format single vehicle."""
        name = data.get("FullVehicleName") or data.get("DisplayName") or "Vozilo"
        plate = data.get("LicencePlate") or data.get("Plate") or "N/A"
        mileage = data.get("Mileage") or data.get("CurrentMileage")
        vin = data.get("VIN")
        driver = data.get("Driver") or data.get("DriverName")
        
        lines = [f"🚗 **{name}**\n"]
        lines.append(f"📋 Registracija: {plate}")
        
        if mileage:
            lines.append(f"📏 Kilometraža: {mileage:,} km")
        if vin:
            lines.append(f"🔑 VIN: {vin}")
        if driver:
            lines.append(f"👤 Vozač: {driver}")
        
        return "\n".join(lines)
    
    def _format_masterdata(self, data: Dict) -> str:
        """Format master data."""
        name = data.get("FullVehicleName") or data.get("DisplayName") or "Vozilo"
        plate = data.get("LicencePlate") or data.get("Plate") or "N/A"
        mileage = data.get("Mileage") or data.get("CurrentMileage")
        vin = data.get("VIN")
        driver = data.get("Driver") or data.get("DriverName")
        
        lines = ["📊 **Podaci o vozilu:**\n"]
        lines.append(f"🚗 {name}")
        lines.append(f"📋 Registracija: {plate}")
        
        if mileage:
            lines.append(f"📏 Kilometraža: **{mileage:,} km**")
        if vin:
            lines.append(f"🔑 VIN: {vin}")
        if driver:
            lines.append(f"👤 Vozač: {driver}")
        
        provider = data.get("ProviderName") or data.get("LeasingProvider")
        monthly = data.get("MonthlyAmount")
        
        if provider or monthly:
            lines.append("\n💼 **Ugovor:**")
            if provider:
                lines.append(f"   Leasing: {provider}")
            if monthly:
                lines.append(f"   Rata: {monthly} EUR/mj")
        
        return "\n".join(lines)
    
    def _format_generic_object(self, data: Dict) -> str:
        """Format generic object."""
        lines = ["📋 **Podaci:**\n"]

        for key, value in list(data.items())[:10]:
            if value is not None and not key.startswith("_"):
                # CRITICAL FIX v12.2: Don't print raw lists/dicts - summarize them
                if isinstance(value, list):
                    if len(value) == 0:
                        lines.append(f"• {key}: (prazno)")
                    elif len(value) == 1 and isinstance(value[0], (str, int, float)):
                        lines.append(f"• {key}: {value[0]}")
                    else:
                        lines.append(f"• {key}: ({len(value)} stavki)")
                elif isinstance(value, dict):
                    # Try to extract meaningful info from nested dict
                    name = value.get("Name") or value.get("DisplayName") or value.get("Title")
                    if name:
                        lines.append(f"• {key}: {name}")
                    else:
                        lines.append(f"• {key}: (objekt)")
                elif isinstance(value, str) and len(value) > 100:
                    # Truncate long strings
                    lines.append(f"• {key}: {value[:100]}...")
                else:
                    lines.append(f"• {key}: {value}")

        return "\n".join(lines)
    
    # Type detection
    
    def _is_vehicle(self, data: Dict) -> bool:
        """Check if data is vehicle."""
        fields = {"VehicleId", "LicencePlate", "Plate", "VIN", "Mileage", "FullVehicleName"}
        return bool(fields & set(data.keys()))
    
    def _is_person(self, data: Dict) -> bool:
        """Check if data is person."""
        fields = {"PersonId", "FirstName", "LastName", "Phone", "Mobile", "Email"}
        return bool(fields & set(data.keys()))
    
    def _is_masterdata(self, data: Dict) -> bool:
        """Check if data is masterdata."""
        has_vehicle = self._is_vehicle(data)
        has_driver = "Driver" in data or "DriverName" in data
        return has_vehicle and has_driver
