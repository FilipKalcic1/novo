"""
Response Formatter
Version: 10.2 (Extended Leasing Keywords)

Formats API responses for WhatsApp.
NO DEPENDENCIES on other services.

v10.2:
- Extended leasing/lizing keyword detection
- More field name variations for provider lookup
- Contract end date support

v10.1:
- Intent-aware formatting based on user query
- Specific responses for kilometraža, registracija, VIN queries
- Falls back to list formatting only when no specific intent detected
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
        tool: Optional[Any] = None,  # Can be UnifiedToolDefinition (Pydantic) or dict
        user_query: Optional[str] = None  # NEW: User's original question for intent-awareness
    ) -> str:
        """
        Format API result for display.

        Args:
            result: Execution result
            tool: Tool metadata (UnifiedToolDefinition or dict)
            user_query: User's original question (for intent-aware formatting)

        Returns:
            Formatted string
        """
        # Store user_query for use in sub-methods
        self._current_query = user_query
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

            # NEW v10.1: Try intent-aware formatting first
            single_response = self._try_format_single_item(items)
            if single_response:
                return single_response

            # Fall back to list formatting
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

                    # NEW v10.1: Try intent-aware formatting first
                    single_response = self._try_format_single_item(nested_data)
                    if single_response:
                        return single_response

                    if self._is_vehicle(nested_data[0]):
                        return self.format_vehicle_list(nested_data)
                    elif self._is_person(nested_data[0]):
                        return self._format_person_list(nested_data)
                    elif self._is_masterdata(nested_data[0]):
                        return self._format_masterdata(nested_data[0])
                    return self._format_generic_list(nested_data, count)
                elif isinstance(nested_data, dict):
                    # NEW v10.1: Try intent-aware formatting for single dict
                    intent_response = self._format_for_query(nested_data)
                    if intent_response:
                        return intent_response

                    if self._is_masterdata(nested_data):
                        return self._format_masterdata(nested_data)
                    elif self._is_vehicle(nested_data):
                        return self._format_vehicle_details(nested_data)
                    return self._format_generic_object(nested_data)

            # CRITICAL FIX: Type guard - only check structure on dict/list, not primitives
            if isinstance(data, dict):
                # NEW v10.1: Try intent-aware formatting
                intent_response = self._format_for_query(data)
                if intent_response:
                    return intent_response

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

                # NEW v10.1: Try intent-aware formatting first
                single_response = self._try_format_single_item(data)
                if single_response:
                    return single_response

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
        mileage = data.get("Mileage") or data.get("CurrentMileage") or data.get("LastMileage")
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
        mileage = data.get("Mileage") or data.get("CurrentMileage") or data.get("LastMileage")
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
        fields = {"VehicleId", "LicencePlate", "Plate", "VIN", "Mileage", "LastMileage", "FullVehicleName"}
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

    # ========== INTENT-AWARE FORMATTING (v10.1) ==========

    def _is_specific_query(self) -> bool:
        """
        Check if user asked for specific information (not a list/selection).

        Examples of specific queries:
        - "kolika mi je kilometraža" → wants mileage number
        - "kada mi istječe registracija" → wants registration date
        - "daj mi podatke o vozilu" → wants vehicle details
        """
        if not self._current_query:
            return False

        q = self._current_query.lower()

        # Keywords indicating user wants specific data, not a selection
        specific_keywords = [
            "kolika", "koliko", "koja", "koji", "što",
            "kada", "kad", "do kada",
            "daj mi", "pokaži mi", "prikaži",
            "kilometraž", "registracij", "istek", "istječe",
            "mileage", "plate", "vin",
            "lizing", "leasing", "rata", "ugovor", "najam"
        ]

        return any(kw in q for kw in specific_keywords)

    def _format_for_query(self, data: Dict) -> Optional[str]:
        """
        Format data based on what user specifically asked for.

        Returns None if no specific intent detected → fall back to default.
        """
        if not self._current_query:
            return None

        q = self._current_query.lower()

        # Extract vehicle info for context
        name = (
            data.get("FullVehicleName") or
            data.get("DisplayName") or
            data.get("Name") or
            "Vaše vozilo"
        )
        plate = data.get("LicencePlate") or data.get("Plate") or ""

        # MILEAGE query
        if any(kw in q for kw in ["kilometraž", "mileage", "koliko km", "koliko kilometara", "km ima"]):
            mileage = data.get("Mileage") or data.get("CurrentMileage") or data.get("LastMileage")
            if mileage:
                return (
                    f"🚗 **{name}**\n"
                    f"📏 Kilometraža: **{mileage:,} km**"
                )
            return f"❌ Kilometraža nije dostupna za {name}."

        # REGISTRATION / PLATE query
        if any(kw in q for kw in ["registracij", "tablice", "tablica", "plate", "oznaka", "broj tablica"]):
            if plate:
                lines = [f"🚗 **{name}**", f"📋 Registracija: **{plate}**"]

                # Add registration expiration if asked
                if any(kw in q for kw in ["istek", "istječe", "do kada", "kada", "vrijedi"]):
                    exp_date = (
                        data.get("RegistrationExpirationDate") or
                        data.get("ExpirationDate") or
                        data.get("RegistrationExpiry")
                    )
                    if exp_date:
                        # Try to format date nicely
                        if isinstance(exp_date, str) and "T" in exp_date:
                            exp_date = exp_date.split("T")[0]
                        lines.append(f"📅 Istek registracije: **{exp_date}**")

                return "\n".join(lines)
            return f"❌ Registracija nije dostupna za vozilo."

        # VIN query
        if "vin" in q:
            vin = data.get("VIN") or data.get("Vin")
            if vin:
                return f"🚗 **{name}**\n🔑 VIN: **{vin}**"
            return f"❌ VIN nije dostupan za {name}."

        # DRIVER query
        if any(kw in q for kw in ["vozač", "driver", "tko vozi", "koji vozač"]):
            driver = data.get("Driver") or data.get("DriverName") or data.get("AssignedDriver")
            if driver:
                return f"🚗 **{name}**\n👤 Vozač: **{driver}**"
            return f"❌ Vozač nije dodijeljen vozilu {name}."

        # LEASING / CONTRACT query - PROŠIRENO za sve varijacije
        if any(kw in q for kw in [
            "leasing", "lizing", "ugovor", "rata", "najam", "contract",
            "mjesečna rata", "provider", "davatelj", "lizing kuć", "leasing kuć"
        ]):
            provider = (
                data.get("ProviderName") or
                data.get("LeasingProvider") or
                data.get("Provider") or
                data.get("LeasingCompany") or
                data.get("Lessor") or
                data.get("LeasingHouse") or
                data.get("ContractProvider")
            )
            monthly = (
                data.get("MonthlyAmount") or
                data.get("MonthlyRate") or
                data.get("MonthlyPayment") or
                data.get("LeaseRate") or
                data.get("MonthlyLease")
            )
            contract_end = (
                data.get("ContractEndDate") or
                data.get("LeaseEndDate") or
                data.get("ContractExpiry")
            )

            if provider or monthly:
                lines = [f"🚗 **{name}**"]
                if provider:
                    lines.append(f"💼 Lizing kuća: **{provider}**")
                if monthly:
                    lines.append(f"💰 Mjesečna rata: **{monthly} EUR**")
                if contract_end:
                    if isinstance(contract_end, str) and "T" in contract_end:
                        contract_end = contract_end.split("T")[0]
                    lines.append(f"📅 Kraj ugovora: {contract_end}")
                return "\n".join(lines)
            return f"❌ Podaci o leasingu nisu dostupni za {name}."

        # GENERAL "my vehicle" / "moje vozilo" query - show summary
        if any(kw in q for kw in ["moje vozilo", "moj auto", "moja kola", "koje vozilo", "koji auto"]):
            return self._format_vehicle_summary(data, name, plate)

        # GENERAL "info" / "podaci" query - show all available
        if any(kw in q for kw in ["podaci", "informacije", "info", "sve o", "detalji"]):
            return self._format_vehicle_summary(data, name, plate)

        # No specific intent detected
        return None

    def _format_vehicle_summary(self, data: Dict, name: str, plate: str) -> str:
        """Format comprehensive vehicle summary."""
        lines = [f"🚗 **{name}**\n"]

        if plate:
            lines.append(f"📋 Registracija: {plate}")

        mileage = data.get("Mileage") or data.get("CurrentMileage") or data.get("LastMileage")
        if mileage:
            lines.append(f"📏 Kilometraža: {mileage:,} km")

        vin = data.get("VIN")
        if vin:
            lines.append(f"🔑 VIN: {vin}")

        driver = data.get("Driver") or data.get("DriverName")
        if driver:
            lines.append(f"👤 Vozač: {driver}")

        exp_date = (
            data.get("RegistrationExpirationDate") or
            data.get("ExpirationDate")
        )
        if exp_date:
            if isinstance(exp_date, str) and "T" in exp_date:
                exp_date = exp_date.split("T")[0]
            lines.append(f"📅 Istek registracije: {exp_date}")

        provider = data.get("ProviderName") or data.get("LeasingProvider")
        monthly = data.get("MonthlyAmount")
        if provider:
            lines.append(f"💼 Leasing: {provider}")
        if monthly:
            lines.append(f"💰 Rata: {monthly} EUR/mj")

        return "\n".join(lines)

    def _try_format_single_item(self, items: List[Dict]) -> Optional[str]:
        """
        Try to format as single item response if user asked specific question.

        Returns None if should show list instead.
        """
        if not items:
            return None

        # Only format single item for specific queries
        if not self._is_specific_query():
            return None

        # Take first item (usually user's own vehicle)
        data = items[0]

        # Try intent-aware formatting
        intent_response = self._format_for_query(data)
        if intent_response:
            return intent_response

        # Fall back to detailed view for single item with specific query
        if len(items) == 1:
            if self._is_masterdata(data):
                return self._format_masterdata(data)
            if self._is_vehicle(data):
                return self._format_vehicle_details(data)

        return None
