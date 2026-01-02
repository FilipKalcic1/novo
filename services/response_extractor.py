"""
LLM Response Extractor - Intelligent data extraction from API responses.
Version: 1.0

Single responsibility: Use LLM to extract ONLY relevant data from API response.
NEVER fabricate data - only return what exists in the response.
"""

import json
import logging
from typing import Dict, Any, Optional, List

from openai import AsyncAzureOpenAI

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class LLMResponseExtractor:
    """
    Uses LLM to extract only the data user asked for from API response.

    Key principles:
    1. NEVER fabricate data - only extract what exists
    2. Format data in user-friendly way
    3. If data doesn't exist, clearly say so
    4. Handle both simple and nested responses
    """

    def __init__(self):
        """Initialize with OpenAI client."""
        self.openai = AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION
        )

    async def extract(
        self,
        user_query: str,
        api_response: Dict[str, Any],
        tool_name: Optional[str] = None,
        extraction_hint: Optional[str] = None
    ) -> str:
        """
        Extract relevant data from API response based on user query.

        Args:
            user_query: Original user question in natural language
            api_response: Raw API response (dict or list)
            tool_name: Name of the tool that was called (for context)
            extraction_hint: Optional hint about which field to extract

        Returns:
            Formatted response string with only relevant data
        """
        logger.info(f"Extracting from response for query: {user_query[:50]}...")

        # Handle empty or error responses
        if not api_response:
            return "Nema podataka za prikaz."

        if isinstance(api_response, dict) and api_response.get("error"):
            return f"Greška: {api_response.get('error')}"

        # Flatten response for easier extraction
        flat_data = self._flatten_response(api_response)

        # If response is very simple (1-3 fields), format directly
        if len(flat_data) <= 3:
            return self._format_simple_response(flat_data, user_query)

        # Use LLM for complex extraction
        try:
            extracted = await self._llm_extract(
                user_query,
                flat_data,
                tool_name,
                extraction_hint
            )
            return extracted
        except Exception as e:
            logger.error(f"LLM extraction failed: {e}")
            # Fallback to simple formatting
            return self._format_fallback(flat_data, user_query)

    def _flatten_response(self, data: Any, prefix: str = "") -> Dict[str, Any]:
        """
        Flatten nested response into simple key-value pairs.

        Example:
            {"vehicle": {"mileage": 14000}} -> {"vehicle.mileage": 14000}
        """
        result = {}

        if isinstance(data, dict):
            for key, value in data.items():
                new_key = f"{prefix}.{key}" if prefix else key

                if isinstance(value, dict):
                    result.update(self._flatten_response(value, new_key))
                elif isinstance(value, list):
                    if value and isinstance(value[0], dict):
                        # List of objects - take first item
                        result.update(self._flatten_response(value[0], f"{new_key}[0]"))
                        result[f"{new_key}.count"] = len(value)
                    else:
                        result[new_key] = value
                else:
                    result[new_key] = value

        elif isinstance(data, list):
            if data and isinstance(data[0], dict):
                result.update(self._flatten_response(data[0], "item[0]"))
                result["items.count"] = len(data)
            else:
                result["items"] = data
        else:
            result["value"] = data

        return result

    def _format_simple_response(self, data: Dict[str, Any], query: str) -> str:
        """Format simple response with few fields."""
        lines = []

        for key, value in data.items():
            if value is not None:
                readable_key = self._humanize_key(key)
                formatted_value = self._format_value(key, value)
                lines.append(f"**{readable_key}:** {formatted_value}")

        return "\n".join(lines) if lines else "Nema podataka."

    async def _llm_extract(
        self,
        query: str,
        data: Dict[str, Any],
        tool_name: Optional[str],
        hint: Optional[str]
    ) -> str:
        """Use LLM to extract only relevant data."""

        # Prepare data summary (limit size)
        data_str = json.dumps(data, ensure_ascii=False, indent=2)
        if len(data_str) > 3000:
            data_str = data_str[:3000] + "\n... (skraćeno)"

        system_prompt = """Ti si asistent za izvlačenje podataka. Tvoj zadatak je:

1. Analiziraj korisnikovo pitanje
2. Pronađi SAMO relevantne podatke u API odgovoru
3. Formatiraj odgovor jasno i koncizno

KRITIČNA PRAVILA:
- NIKADA ne izmišljaj podatke koji ne postoje u odgovoru
- Ako traženi podatak ne postoji, reci "Podatak nije dostupan"
- Koristi hrvatski jezik
- Formatiraj brojeve čitljivo (14328 km, ne 14328)
- Datume formatiraj kao DD.MM.YYYY

PRIMJERI:

Pitanje: "kolika mi je kilometraža"
Podaci: {"LastMileage": 14328, "LicencePlate": "ZG-1234-AB", "Name": "Golf"}
Odgovor: 📏 **Kilometraža:** 14.328 km

Pitanje: "kada istječe registracija"
Podaci: {"RegistrationExpirationDate": "2025-05-15", "Mileage": 50000}
Odgovor: 📅 **Registracija istječe:** 15.05.2025

Pitanje: "podaci o vozilu"
Podaci: {"FullVehicleName": "VW Golf", "LicencePlate": "ZG-1234-AB", "Mileage": 14328}
Odgovor:
🚗 **Vozilo:** VW Golf
🔢 **Tablica:** ZG-1234-AB
📏 **Kilometraža:** 14.328 km

Pitanje: "kada istječe registracija"
Podaci: {"Mileage": 50000, "Name": "Golf"}
Odgovor: ❌ Podatak o isteku registracije nije dostupan u sustavu."""

        user_prompt = f"""PITANJE: {query}

DOSTUPNI PODACI:
{data_str}

{f"HINT: Fokusiraj se na polje '{hint}'" if hint else ""}

Izvuci SAMO ono što korisnik traži. Budi koncizan."""

        try:
            response = await self.openai.chat.completions.create(
                model=settings.AZURE_OPENAI_DEPLOYMENT_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,
                max_tokens=500
            )

            content = response.choices[0].message.content.strip()

            # VALIDATION: Check for potential hallucination indicators
            self._validate_extraction(content, data, query)

            return content

        except Exception as e:
            logger.error(f"LLM extraction error: {e}")
            raise

    def _validate_extraction(self, extracted: str, original_data: Dict[str, Any], query: str) -> None:
        """
        Validate that extracted data doesn't contain hallucinated values.

        Logs warnings if potential hallucinations are detected.
        """
        import re

        # Check for suspicious patterns that might indicate hallucination

        # 1. Check for vehicle count claims
        count_match = re.search(r'(\d+)\s*(vozil|auto)', extracted.lower())
        if count_match:
            claimed_count = int(count_match.group(1))
            # Check if Data field exists with actual count
            actual_data = original_data.get("Data", original_data)
            if isinstance(actual_data, list):
                actual_count = len(actual_data)
                if claimed_count != actual_count:
                    logger.warning(
                        f"POTENTIAL HALLUCINATION: LLM claimed {claimed_count} vehicles, "
                        f"but API returned {actual_count}. Query: '{query[:50]}'"
                    )

        # 2. Check for registration plates not in original data
        plate_matches = re.findall(r'[A-Z]{2,3}[-\s]?\d{3,4}[-\s]?[A-Z]{1,2}', extracted)
        if plate_matches:
            data_str = json.dumps(original_data).upper()
            for plate in plate_matches:
                # Normalize plate for comparison
                normalized = plate.replace("-", "").replace(" ", "")
                if normalized not in data_str.replace("-", "").replace(" ", ""):
                    logger.warning(
                        f"POTENTIAL HALLUCINATION: Plate '{plate}' not found in API data. "
                        f"Query: '{query[:50]}'"
                    )

    def _format_fallback(self, data: Dict[str, Any], query: str) -> str:
        """Fallback formatting when LLM fails."""
        query_lower = query.lower()

        # Try to match known patterns
        patterns = {
            "kilometra": ["LastMileage", "Mileage", "CurrentMileage", "mileage"],
            "registraci": ["RegistrationExpirationDate", "RegistrationExpiry", "ExpirationDate"],
            "tablice": ["LicencePlate", "Plate", "RegistrationNumber"],
            "vozilo": ["FullVehicleName", "Name", "VehicleName", "DisplayName"],
            "lizing": ["LeasingProvider", "Leasing", "LeasingCompany"],
        }

        for keyword, fields in patterns.items():
            if keyword in query_lower:
                for field in fields:
                    # Check both direct and nested keys
                    for key, value in data.items():
                        if field.lower() in key.lower() and value is not None:
                            readable_key = self._humanize_key(field)
                            formatted = self._format_value(key, value)
                            return f"**{readable_key}:** {formatted}"

        # Generic fallback - show first 5 non-null fields
        lines = []
        count = 0
        for key, value in data.items():
            if value is not None and count < 5:
                readable_key = self._humanize_key(key)
                formatted = self._format_value(key, value)
                lines.append(f"**{readable_key}:** {formatted}")
                count += 1

        return "\n".join(lines) if lines else "Nema podataka za prikaz."

    def _humanize_key(self, key: str) -> str:
        """Convert API key to human-readable Croatian label."""
        translations = {
            # Mileage
            "LastMileage": "Kilometraža",
            "Mileage": "Kilometraža",
            "CurrentMileage": "Trenutna kilometraža",
            "LastMileageTime": "Vrijeme unosa km",

            # Vehicle
            "FullVehicleName": "Vozilo",
            "VehicleName": "Vozilo",
            "Name": "Naziv",
            "DisplayName": "Naziv",
            "LicencePlate": "Registarska oznaka",
            "Plate": "Tablica",
            "VIN": "VIN broj",

            # Registration
            "RegistrationExpirationDate": "Istek registracije",
            "RegistrationExpiry": "Istek registracije",
            "ExpirationDate": "Datum isteka",

            # Leasing
            "LeasingProvider": "Lizing kuća",
            "LeasingCompany": "Lizing kuća",

            # Person
            "PersonName": "Ime",
            "DisplayName": "Ime i prezime",
            "Email": "Email",
            "Phone": "Telefon",

            # Booking
            "FromTime": "Od",
            "ToTime": "Do",
            "Status": "Status",
            "Description": "Opis",
        }

        # Remove prefix (e.g., "vehicle.mileage" -> "mileage")
        simple_key = key.split(".")[-1].replace("[0]", "")

        return translations.get(simple_key, simple_key)

    def _format_value(self, key: str, value: Any) -> str:
        """Format value based on key type."""
        if value is None:
            return "N/A"

        key_lower = key.lower()

        # Mileage - add units and thousand separators
        if "mileage" in key_lower or key == "Value":
            try:
                num = int(float(value))
                formatted = f"{num:,}".replace(",", ".")
                return f"{formatted} km"
            except (ValueError, TypeError):
                return str(value)

        # Dates - format as DD.MM.YYYY
        if "date" in key_lower or "time" in key_lower or "expir" in key_lower:
            if isinstance(value, str) and "T" in value:
                try:
                    date_part = value.split("T")[0]
                    parts = date_part.split("-")
                    if len(parts) == 3:
                        return f"{parts[2]}.{parts[1]}.{parts[0]}"
                except:
                    pass
            return str(value)

        # Boolean
        if isinstance(value, bool):
            return "Da" if value else "Ne"

        # Lists
        if isinstance(value, list):
            if len(value) <= 3:
                return ", ".join(str(v) for v in value)
            return f"{len(value)} stavki"

        return str(value)


# Singleton instance
_extractor = None


def get_response_extractor() -> LLMResponseExtractor:
    """Get singleton instance of response extractor."""
    global _extractor
    if _extractor is None:
        _extractor = LLMResponseExtractor()
    return _extractor
