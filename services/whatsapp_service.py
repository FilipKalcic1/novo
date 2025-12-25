"""
WhatsApp Integration Service
Version: 1.0

KRITIČNA KOMPONENTA - Rješava probleme:
1. Phone vs UUID trap: Validira da 'to' sadrži telefonski broj, ne UUID
2. Payload struktura: Infobip zahtijeva specifičnu strukturu
3. UTF-8 encoding: Osigurava sigurno enkodiranje poruka
4. Type guards: Sprječava slanje objekta umjesto stringa
5. Rate limiting & Backoff: Exponential backoff za 429 errore
6. Deep logging: Logira payload prije slanja

INFOBIP ZAHTJEVI:
- Content-Type: application/json
- Authorization: App {API_KEY}
- Payload: {"from": "...", "to": "...", "content": {"text": "..."}}
- 'to' MORA biti telefonski broj (385...), ne UUID!
"""

import asyncio
import json
import logging
import random
import re
import unicodedata
from typing import Optional, Dict, Any, Tuple, List
from dataclasses import dataclass
from datetime import datetime

import httpx

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION PATTERNS
# ═══════════════════════════════════════════════════════════════════════════════

# UUID pattern (v4): 8-4-4-4-12 hex characters
UUID_PATTERN = re.compile(
    r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
)

# Phone number pattern (international format)
# Accepts: +385..., 385..., 00385..., etc.
PHONE_PATTERN = re.compile(
    r'^(\+)?[0-9]{10,15}$'
)

# Croatian phone specifically
CROATIAN_PHONE_PATTERN = re.compile(
    r'^(\+385|385|00385|0)[1-9][0-9]{7,8}$'
)


@dataclass
class SendResult:
    """Result of WhatsApp send operation."""
    success: bool
    message_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    status_code: Optional[int] = None
    retry_after: Optional[int] = None  # For rate limiting


class WhatsAppService:
    """
    Production-grade WhatsApp integration service.

    Features:
    - Phone number validation (prevents UUID trap)
    - UTF-8 safe encoding
    - Type guards (string enforcement)
    - Exponential backoff with jitter
    - Deep logging for debugging
    - Message chunking for long messages
    """

    # Infobip limits
    MAX_MESSAGE_LENGTH = 4096  # WhatsApp text limit
    MAX_RETRIES = 3
    BASE_DELAY = 1.0  # Base delay for exponential backoff
    MAX_JITTER = 0.5  # Random jitter (0-0.5 seconds)

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        sender_number: Optional[str] = None
    ):
        """
        Initialize WhatsApp service.

        Args:
            api_key: Infobip API key (defaults to settings)
            base_url: Infobip base URL (defaults to settings)
            sender_number: Sender phone number (defaults to settings)
        """
        self.api_key = api_key or settings.INFOBIP_API_KEY
        self.base_url = base_url or settings.INFOBIP_BASE_URL
        self.sender_number = sender_number or settings.INFOBIP_SENDER_NUMBER

        # Validate configuration
        self._validate_config()

        # Stats
        self._messages_sent = 0
        self._messages_failed = 0
        self._total_retries = 0

        logger.info(
            f"WhatsAppService initialized: "
            f"base_url={self.base_url}, "
            f"sender={self.sender_number[-4:] if self.sender_number else 'N/A'}"
        )

    def _validate_config(self) -> None:
        """Validate configuration at startup."""
        if not self.api_key:
            logger.warning(
                "INFOBIP_API_KEY not configured! "
                "WhatsApp sending will be disabled."
            )

        if not self.sender_number:
            logger.warning(
                "INFOBIP_SENDER_NUMBER not configured! "
                "WhatsApp sending will fail."
            )

        if self.api_key and len(self.api_key) < 10:
            logger.warning(
                "INFOBIP_API_KEY appears to be invalid (too short)."
            )

    # ═══════════════════════════════════════════════════════════════════════════════
    # VALIDATION METHODS (CRITICAL FOR 400 ERROR PREVENTION)
    # ═══════════════════════════════════════════════════════════════════════════════

    def validate_phone_number(self, number: str) -> Tuple[bool, str, Optional[str]]:
        """
        Validate phone number and detect UUID trap.

        KRITIČNO: Ovo sprječava slanje poruke na UUID umjesto telefonskog broja!

        Args:
            number: Phone number to validate

        Returns:
            Tuple of (is_valid, normalized_number, error_message)

        Examples:
            "+385991234567" -> (True, "385991234567", None)
            "00385991234567" -> (True, "385991234567", None)
            "550e8400-e29b-..." -> (False, None, "UUID detected in phone field!")
        """
        if not number:
            return (False, "", "Phone number is empty")

        # Clean whitespace
        number = number.strip()

        # CRITICAL: Detect UUID trap
        if UUID_PATTERN.match(number):
            logger.error(
                f"UUID TRAP DETECTED! "
                f"Field 'to' contains UUID instead of phone number: {number[:20]}..."
            )
            return (
                False,
                "",
                f"UUID detected in phone field! Expected phone number, got: {number[:20]}..."
            )

        # Remove common prefixes and normalize
        normalized = number

        # Remove + prefix (Infobip prefers without it)
        if normalized.startswith('+'):
            normalized = normalized[1:]

        # Remove 00 prefix
        if normalized.startswith('00'):
            normalized = normalized[2:]

        # Remove leading 0 for Croatian numbers
        if normalized.startswith('0') and len(normalized) == 10:
            # Convert 091234567 to 385991234567
            normalized = '385' + normalized[1:]

        # Validate format
        if not PHONE_PATTERN.match(normalized):
            return (
                False,
                "",
                f"Invalid phone number format: {number}"
            )

        # Additional Croatian validation
        if normalized.startswith('385'):
            if len(normalized) < 11 or len(normalized) > 12:
                return (
                    False,
                    "",
                    f"Croatian phone number has invalid length: {number}"
                )

        logger.debug(f"Phone validated: {number} -> {normalized}")
        return (True, normalized, None)

    def ensure_string(self, value: Any) -> Tuple[str, bool]:
        """
        Ensure value is a string, not an object.

        KRITIČNO: AI može vratiti objekt umjesto stringa:
        {"odgovor": "Vozilo je Audi"} -> "Vozilo je Audi"

        Args:
            value: Value to convert to string

        Returns:
            Tuple of (string_value, was_converted)

        Examples:
            "Hello" -> ("Hello", False)
            {"text": "Hello"} -> ('{"text": "Hello"}' OR extracted text, True)
            123 -> ("123", True)
        """
        if isinstance(value, str):
            return (value, False)

        if value is None:
            logger.warning("ensure_string received None, returning empty string")
            return ("", True)

        # Handle dict - try to extract text intelligently
        if isinstance(value, dict):
            logger.warning(
                f"TYPE GUARD: Received dict instead of string: {list(value.keys())}"
            )

            # Try common text keys
            for key in ['text', 'message', 'content', 'odgovor', 'response', 'answer']:
                if key in value and isinstance(value[key], str):
                    logger.info(f"Extracted text from dict key '{key}'")
                    return (value[key], True)

            # Fallback to JSON serialization
            try:
                return (json.dumps(value, ensure_ascii=False), True)
            except Exception:
                return (str(value), True)

        # Handle list
        if isinstance(value, list):
            logger.warning(
                f"TYPE GUARD: Received list instead of string: {len(value)} items"
            )

            # Try to join string items
            if all(isinstance(item, str) for item in value):
                return ("\n".join(value), True)

            try:
                return (json.dumps(value, ensure_ascii=False), True)
            except Exception:
                return (str(value), True)

        # Handle other types
        return (str(value), True)

    def ensure_utf8_safe(self, text: str) -> str:
        """
        Ensure text is UTF-8 safe for WhatsApp.

        KRITIČNO: WhatsApp može odbiti poruke s nevalidnim UTF-8 znakovima.

        This function:
        1. Normalizes Unicode (NFC)
        2. Removes control characters (except newline, tab)
        3. Replaces invalid sequences
        4. Handles emoji properly

        Args:
            text: Text to sanitize

        Returns:
            UTF-8 safe text
        """
        if not text:
            return ""

        # Step 1: Normalize Unicode (combine characters properly)
        text = unicodedata.normalize('NFC', text)

        # Step 2: Remove control characters (except \n, \t, \r)
        cleaned = []
        for char in text:
            # Keep printable characters and common whitespace
            if char in '\n\t\r':
                cleaned.append(char)
            elif unicodedata.category(char) == 'Cc':
                # Control character - skip
                continue
            else:
                cleaned.append(char)

        text = ''.join(cleaned)

        # Step 3: Encode/decode to ensure valid UTF-8
        try:
            text = text.encode('utf-8', errors='replace').decode('utf-8')
        except Exception as e:
            logger.warning(f"UTF-8 encoding issue: {e}")
            # Last resort: strip non-ASCII
            text = text.encode('ascii', errors='ignore').decode('ascii')

        return text

    # ═══════════════════════════════════════════════════════════════════════════════
    # PAYLOAD BUILDING (INFOBIP SPECIFIC)
    # ═══════════════════════════════════════════════════════════════════════════════

    def build_payload(
        self,
        to: str,
        text: str,
        from_number: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Build Infobip-compliant payload.

        INFOBIP FORMAT:
        {
            "from": "385...",
            "to": "385...",
            "content": {
                "text": "Message text here"
            }
        }

        NOT the messages[] wrapper - that's for batch sending.

        Args:
            to: Recipient phone number (must be validated)
            text: Message text (must be string)
            from_number: Optional sender number

        Returns:
            Infobip-compliant payload
        """
        payload = {
            "from": from_number or self.sender_number,
            "to": to,
            "content": {
                "text": text
            }
        }

        return payload

    def build_headers(self) -> Dict[str, str]:
        """
        Build Infobip headers.

        KRITIČNO:
        - Authorization mora biti "App {API_KEY}" (ne "Bearer")
        - Content-Type mora biti application/json
        """
        if not self.api_key:
            logger.error("Cannot build headers: API_KEY is empty!")
            return {}

        return {
            "Authorization": f"App {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    # ═══════════════════════════════════════════════════════════════════════════════
    # SENDING WITH RETRY (EXPONENTIAL BACKOFF + JITTER)
    # ═══════════════════════════════════════════════════════════════════════════════

    async def send(
        self,
        to: str,
        text: Any,
        validate: bool = True
    ) -> SendResult:
        """
        Send WhatsApp message with full validation and retry logic.

        Args:
            to: Recipient phone number
            text: Message text (will be converted to string if needed)
            validate: Whether to validate phone number

        Returns:
            SendResult with success/failure info
        """
        # Step 1: Validate phone number
        if validate:
            is_valid, normalized_to, error = self.validate_phone_number(to)
            if not is_valid:
                logger.error(f"Phone validation failed: {error}")
                return SendResult(
                    success=False,
                    error_code="INVALID_PHONE",
                    error_message=error
                )
            to = normalized_to

        # Step 2: Ensure text is string
        text_str, was_converted = self.ensure_string(text)
        if was_converted:
            logger.warning(f"Text was converted from {type(text).__name__} to string")

        # Step 3: Ensure UTF-8 safe
        text_str = self.ensure_utf8_safe(text_str)

        # Step 4: Check length
        if len(text_str) > self.MAX_MESSAGE_LENGTH:
            logger.warning(
                f"Message too long ({len(text_str)} chars), truncating to {self.MAX_MESSAGE_LENGTH}"
            )
            text_str = text_str[:self.MAX_MESSAGE_LENGTH - 3] + "..."

        # Step 5: Build payload
        payload = self.build_payload(to, text_str)
        headers = self.build_headers()

        if not headers:
            return SendResult(
                success=False,
                error_code="CONFIG_ERROR",
                error_message="API key not configured"
            )

        # DEEP LOGGING: Log payload before sending
        logger.info(
            f"SENDING TO INFOBIP: "
            f"to={to[-4:]}..., "
            f"text_length={len(text_str)}, "
            f"payload={json.dumps(payload, ensure_ascii=False)[:200]}"
        )

        # Step 6: Send with retry
        url = f"https://{self.base_url}/whatsapp/1/message/text"

        return await self._send_with_retry(url, payload, headers)

    async def _send_with_retry(
        self,
        url: str,
        payload: Dict[str, Any],
        headers: Dict[str, str]
    ) -> SendResult:
        """
        Send with exponential backoff and jitter.

        Retry on:
        - 429 Rate Limit
        - 5xx Server Errors
        - Network timeouts
        """
        last_error = None

        for attempt in range(self.MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        url,
                        json=payload,
                        headers=headers
                    )

                # Success
                if response.status_code in (200, 201):
                    self._messages_sent += 1

                    try:
                        response_data = response.json()
                        message_id = response_data.get("messages", [{}])[0].get("messageId")
                    except Exception:
                        message_id = None

                    logger.info(
                        f"Message sent successfully: "
                        f"to={payload['to'][-4:]}..., "
                        f"message_id={message_id}"
                    )

                    return SendResult(
                        success=True,
                        message_id=message_id,
                        status_code=response.status_code
                    )

                # Rate limit - MUST retry with backoff
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 0))

                    if attempt < self.MAX_RETRIES - 1:
                        delay = self._calculate_backoff(attempt, retry_after)
                        logger.warning(
                            f"Rate limited (429). Retry {attempt + 1}/{self.MAX_RETRIES} "
                            f"after {delay:.2f}s"
                        )
                        self._total_retries += 1
                        await asyncio.sleep(delay)
                        continue
                    else:
                        return SendResult(
                            success=False,
                            error_code="RATE_LIMIT",
                            error_message="Rate limit exceeded after max retries",
                            status_code=429,
                            retry_after=retry_after
                        )

                # Server error - retry
                if response.status_code >= 500:
                    if attempt < self.MAX_RETRIES - 1:
                        delay = self._calculate_backoff(attempt)
                        logger.warning(
                            f"Server error ({response.status_code}). "
                            f"Retry {attempt + 1}/{self.MAX_RETRIES} after {delay:.2f}s"
                        )
                        self._total_retries += 1
                        await asyncio.sleep(delay)
                        continue

                # Client error - don't retry (except 429)
                self._messages_failed += 1

                try:
                    error_data = response.json()
                    error_message = error_data.get("requestError", {}).get(
                        "serviceException", {}
                    ).get("text", str(error_data))
                except Exception:
                    error_message = response.text

                logger.error(
                    f"Send failed: status={response.status_code}, "
                    f"error={error_message[:200]}"
                )

                return SendResult(
                    success=False,
                    error_code=f"HTTP_{response.status_code}",
                    error_message=error_message,
                    status_code=response.status_code
                )

            except httpx.TimeoutException as e:
                last_error = str(e)
                if attempt < self.MAX_RETRIES - 1:
                    delay = self._calculate_backoff(attempt)
                    logger.warning(
                        f"Timeout. Retry {attempt + 1}/{self.MAX_RETRIES} "
                        f"after {delay:.2f}s"
                    )
                    self._total_retries += 1
                    await asyncio.sleep(delay)
                    continue

            except Exception as e:
                last_error = str(e)
                logger.error(f"Send error: {e}")

                if attempt < self.MAX_RETRIES - 1:
                    delay = self._calculate_backoff(attempt)
                    logger.warning(
                        f"Retrying after error. Retry {attempt + 1}/{self.MAX_RETRIES} "
                        f"after {delay:.2f}s"
                    )
                    self._total_retries += 1
                    await asyncio.sleep(delay)
                    continue

        # All retries exhausted
        self._messages_failed += 1

        return SendResult(
            success=False,
            error_code="MAX_RETRIES_EXCEEDED",
            error_message=f"Failed after {self.MAX_RETRIES} attempts: {last_error}"
        )

    def _calculate_backoff(
        self,
        attempt: int,
        min_delay: int = 0
    ) -> float:
        """
        Calculate exponential backoff with jitter.

        Formula: max(min_delay, 2^attempt * base) + random(0, jitter)
        """
        exponential_delay = (2 ** attempt) * self.BASE_DELAY
        jitter = random.uniform(0, self.MAX_JITTER)

        return max(min_delay, exponential_delay) + jitter

    # ═══════════════════════════════════════════════════════════════════════════════
    # BATCH SENDING (FOR FUTURE USE)
    # ═══════════════════════════════════════════════════════════════════════════════

    async def send_batch(
        self,
        messages: List[Tuple[str, str]]
    ) -> List[SendResult]:
        """
        Send multiple messages with rate limiting.

        Args:
            messages: List of (to, text) tuples

        Returns:
            List of SendResult
        """
        results = []

        for to, text in messages:
            result = await self.send(to, text)
            results.append(result)

            # Small delay between messages to avoid rate limiting
            await asyncio.sleep(0.1)

        return results

    # ═══════════════════════════════════════════════════════════════════════════════
    # STATS & HEALTH
    # ═══════════════════════════════════════════════════════════════════════════════

    def get_stats(self) -> Dict[str, Any]:
        """Get service statistics."""
        return {
            "messages_sent": self._messages_sent,
            "messages_failed": self._messages_failed,
            "total_retries": self._total_retries,
            "success_rate": (
                self._messages_sent / (self._messages_sent + self._messages_failed)
                if (self._messages_sent + self._messages_failed) > 0
                else 0.0
            )
        }

    def health_check(self) -> Dict[str, Any]:
        """Check service health."""
        return {
            "healthy": bool(self.api_key and self.sender_number),
            "api_key_configured": bool(self.api_key),
            "sender_configured": bool(self.sender_number),
            "base_url": self.base_url
        }
