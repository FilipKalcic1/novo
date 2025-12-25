"""
Error Parser - AI Feedback Generator
Version: 2.0

Converts API errors into Croatian explanations for LLM self-correction.

GATE 3: CIRCUIT BREAKER - If API returns 4xx/5xx, generate structured feedback.
GATE 5: EXTERNAL API FEEDBACK - Parse raw errors into actionable hints.

NO business logic - purely error interpretation.
"""

import logging
from typing import Dict, Any, Optional


logger = logging.getLogger(__name__)


class ErrorParser:
    """
    Parses API errors and generates AI-friendly feedback in Croatian.

    Pattern:
    - HTTP status -> Croatian explanation
    - API error message -> Actionable hint
    - Missing parameters -> Tool suggestion
    """

    @staticmethod
    def parse_http_error(
        status_code: int,
        response_body: Any,
        operation_id: str
    ) -> str:
        """
        Parse HTTP error into Croatian feedback.

        Args:
            status_code: HTTP status code
            response_body: Response body (may be dict, string, or None)
            operation_id: Tool that failed

        Returns:
            Croatian explanation for LLM
        """
        # Extract error message from response
        error_message = ErrorParser._extract_error_message(response_body)

        # Map status code to explanation
        if status_code == 400:
            return (
                f"Neispravni parametri za '{operation_id}'. "
                f"Detalji: {error_message}. "
                f"Provjeri tipove podataka i obavezna polja."
            )

        elif status_code == 401:
            return (
                f"Autentifikacija nije uspjela za '{operation_id}'. "
                f"Token je možda istekao. Ovo je sistemska greška."
            )

        elif status_code == 403:
            # Provide more specific feedback based on operation type
            if any(x in operation_id.lower() for x in ["booking", "calendar", "reservation"]):
                return (
                    f"Pristup odbijen za '{operation_id}'. "
                    f"Korisnik nema dozvolu za rezervaciju ovog vozila. "
                    f"Moguće da vozilo nije dostupno za ovog korisnika ili "
                    f"korisnik nema pravo na rezervaciju. "
                    f"Detalji: {error_message}."
                )
            elif any(x in operation_id.lower() for x in ["delete", "remove"]):
                return (
                    f"Pristup odbijen za '{operation_id}'. "
                    f"Korisnik nema dozvolu za brisanje ovog resursa. "
                    f"Detalji: {error_message}."
                )
            else:
                return (
                    f"Pristup odbijen za '{operation_id}'. "
                    f"Korisnik nema dozvolu za ovu operaciju. "
                    f"Detalji: {error_message}."
                )

        elif status_code == 404:
            return (
                f"Resurs nije pronađen za '{operation_id}'. "
                f"ID je neispravan ili resurs ne postoji. "
                f"Detalji: {error_message}."
            )

        elif status_code == 405:
            return (
                f"HTTP metoda nije dozvoljena za '{operation_id}'. "
                f"Provjerite da li endpoint podržava ovu operaciju. "
                f"Možda pokušavate koristiti POST na GET endpoint ili obrnuto. "
                f"Detalji: {error_message}."
            )

        elif status_code == 422:
            return (
                f"Validacija nije uspjela za '{operation_id}'. "
                f"Detalji: {error_message}. "
                f"Provjeri format podataka (datum, email, itd.)."
            )

        elif status_code == 429:
            return (
                f"Previše zahtjeva za '{operation_id}'. "
                f"Pokušaj ponovno za nekoliko sekundi."
            )

        elif 500 <= status_code < 600:
            return (
                f"Greška servera pri izvršavanju '{operation_id}'. "
                f"Detalji: {error_message}. "
                f"Pokušaj ponovno ili koristi alternativni alat."
            )

        else:
            return (
                f"Nepoznata greška (HTTP {status_code}) za '{operation_id}'. "
                f"Detalji: {error_message}."
            )

    @staticmethod
    def _extract_error_message(response_body: Any) -> str:
        """Extract error message from response body."""
        if not response_body:
            return "Nema dodatnih detalja"

        if isinstance(response_body, str):
            return response_body[:200]

        if isinstance(response_body, dict):
            # Try common error fields
            for key in ["message", "error", "detail", "Message", "Error", "Detail"]:
                if key in response_body:
                    msg = response_body[key]
                    if isinstance(msg, str):
                        return msg[:200]
                    return str(msg)[:200]

            # Validation errors
            if "errors" in response_body:
                errors = response_body["errors"]
                if isinstance(errors, dict):
                    error_list = []
                    for field, msgs in errors.items():
                        if isinstance(msgs, list):
                            error_list.append(f"{field}: {', '.join(msgs)}")
                        else:
                            error_list.append(f"{field}: {msgs}")
                    return "; ".join(error_list)[:200]

            # Return first 200 chars of body
            return str(response_body)[:200]

        return "Nema dodatnih detalja"

    @staticmethod
    def generate_missing_param_feedback(
        missing_params: list,
        suggested_tools: list = None
    ) -> str:
        """
        Generate feedback for missing parameters.

        Args:
            missing_params: List of missing parameter names
            suggested_tools: Tools that can provide these params

        Returns:
            Croatian feedback
        """
        feedback = f"Nedostaju obavezni parametri: {', '.join(missing_params)}."

        if suggested_tools:
            feedback += (
                f" Preporučeni alati za dohvat podataka: "
                f"{', '.join(suggested_tools)}."
            )
        else:
            feedback += " Zatraži te podatke od korisnika."

        return feedback

    @staticmethod
    def generate_type_error_feedback(
        param_name: str,
        expected_type: str,
        received_value: Any
    ) -> str:
        """
        Generate feedback for type mismatch.

        Args:
            param_name: Parameter name
            expected_type: Expected type
            received_value: Value received

        Returns:
            Croatian feedback
        """
        return (
            f"Parametar '{param_name}' mora biti tipa {expected_type}, "
            f"ali je dobivena vrijednost: {received_value}. "
            f"Pretvori vrijednost u odgovarajući tip."
        )

    @staticmethod
    def generate_hallucination_warning(
        param_name: str,
        available_sources: list
    ) -> str:
        """
        Generate warning against hallucinating IDs.

        Args:
            param_name: Parameter name (e.g., 'VehicleId')
            available_sources: Where to get this param

        Returns:
            Croatian warning
        """
        sources_text = ", ".join(available_sources)
        return (
            f"UPOZORENJE: Parametar '{param_name}' ne smije biti izmišljen! "
            f"Dohvati ga iz: {sources_text}."
        )
