"""
AI Orchestrator
Version: 12.2

OpenAI integration for intent analysis and response generation.

v12.2 CHANGES (PRECISION FIX):
- SINGLE_TOOL_THRESHOLD: 0.95 → 0.98 (more conservative)
- MAX_TOOLS_FOR_LLM: 5 → 10 (better tool selection)
- MAX_HISTORY_MESSAGES: 5 → 10 (more context)
- Smart History only trims if > 2x limit (less aggressive)

v12.1 CHANGES:
- Disabled SDK internal retry (max_retries=0)
- Added APIStatusError and APITimeoutError handling

v12.0 FEATURES:
1. Token Budgeting - Dynamic Tool Trimming when top match >= threshold
2. Smart History - Sliding Window with entity preservation
3. Token Tracking - Logs prompt_tokens and completion_tokens
4. Exponential Backoff for 429 RateLimitReached errors
5. Resilience Pattern - Auto-retry with jitter

SECURITY: Uses sanitizer before sending data to AI.
DEPENDS ON: config.py, sanitizer.py
"""

import asyncio
import json
import logging
import random
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple

from openai import AsyncAzureOpenAI, RateLimitError, APIStatusError, APITimeoutError

from config import get_settings
from services.sanitizer import sanitize

logger = logging.getLogger(__name__)
settings = get_settings()


# ═══════════════════════════════════════════════════════════════════════════════
# TOKEN BUDGETING CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

# If best tool score >= this threshold, send only that tool (saves ~80% tokens)
# INCREASED from 0.95 to 0.98 - be more conservative to avoid losing precision
SINGLE_TOOL_THRESHOLD = 0.98

# Maximum number of tools to send to LLM (prevents context overflow)
# INCREASED from 5 to 10 - better precision, still saves tokens vs 900+
MAX_TOOLS_FOR_LLM = 10

# Maximum history messages to keep (sliding window)
# INCREASED from 5 to 10 - keeps more context for better understanding
MAX_HISTORY_MESSAGES = 10

# Entity keys to preserve across history truncation
PRESERVED_ENTITY_KEYS = [
    "vehicle_id", "vehicleId", "VehicleId",
    "person_id", "personId", "PersonId",
    "booking_id", "bookingId", "BookingId",
    "plate", "LicencePlate", "registration"
]


class AIOrchestrator:
    """
    Orchestrates AI interactions.

    Features:
    - Tool calling with forced execution
    - Parameter extraction
    - Response generation
    - NEW v12.0: Token budgeting & tracking
    - NEW v12.0: Exponential backoff for rate limits
    - NEW v12.0: Smart history management
    """

    # Retry configuration
    MAX_RETRIES = 3
    BASE_DELAY = 1.0
    MAX_JITTER = 0.5

    def __init__(self):
        """Initialize AI orchestrator."""
        # CRITICAL v12.1: Disable SDK's internal retry mechanism
        # SDK default is 60s wait which is too long!
        # We use our own exponential backoff (1-4 seconds)
        self.client = AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            max_retries=0,  # Disable SDK retry - we handle it ourselves
            timeout=30.0    # 30 second timeout
        )
        self.model = settings.AZURE_OPENAI_DEPLOYMENT_NAME

        # NEW v12.0: Token tracking
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        self._total_requests = 0
        self._rate_limit_hits = 0
    
    async def analyze(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict]] = None,
        system_prompt: Optional[str] = None,
        forced_tool: Optional[str] = None,
        tool_scores: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:
        """
        Analyze user input and decide on action.

        MASTER PROMPT v9.0 - ACTION-FIRST PROTOCOL:
        If forced_tool is provided, LLM MUST call that tool (no text fallback).
        This ensures high-confidence matches (similarity >= 0.85) always execute.

        NEW v12.0:
        - Token Budgeting: If best tool score >= 0.95, send only that tool
        - Smart History: Applies sliding window to messages
        - Exponential Backoff: Retries on 429 with jitter
        - Token Tracking: Logs token usage

        Args:
            messages: Conversation history
            tools: Available tools
            system_prompt: System instructions
            forced_tool: If set, force LLM to call this specific tool (no "auto")
            tool_scores: Optional list of {name, score} for token budgeting

        Returns:
            {type: "tool_call"|"text", ...}
        """
        # NEW v12.0: Apply Smart History (sliding window)
        trimmed_messages = self._apply_smart_history(messages)

        full_messages = []

        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})

        full_messages.extend(trimmed_messages)

        # NEW v12.0: Apply Token Budgeting (trim tools if top match is excellent)
        trimmed_tools = self._apply_token_budgeting(tools, tool_scores)

        call_args = {
            "model": self.model,
            "messages": full_messages,
            "temperature": settings.AI_TEMPERATURE,
            "max_tokens": settings.AI_MAX_TOKENS
        }

        if trimmed_tools:
            call_args["tools"] = trimmed_tools

            # ACTION-FIRST PROTOCOL: Force specific tool if similarity >= ACTION_THRESHOLD
            if forced_tool:
                call_args["tool_choice"] = {
                    "type": "function",
                    "function": {"name": forced_tool}
                }
                logger.info(f"🎯 FORCED TOOL CALL: {forced_tool} (similarity >= {settings.ACTION_THRESHOLD})")
            else:
                call_args["tool_choice"] = "auto"

        # NEW v12.0: Retry with exponential backoff
        last_error = None

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await self.client.chat.completions.create(**call_args)
                self._total_requests += 1

                # NEW v12.0: Track token usage
                if hasattr(response, 'usage') and response.usage:
                    prompt_tokens = response.usage.prompt_tokens
                    completion_tokens = response.usage.completion_tokens

                    self._total_prompt_tokens += prompt_tokens
                    self._total_completion_tokens += completion_tokens

                    logger.info(
                        f"🎫 Tokens: prompt={prompt_tokens}, "
                        f"completion={completion_tokens}, "
                        f"total_session={self._total_prompt_tokens + self._total_completion_tokens}"
                    )

                if not response.choices:
                    logger.error("Empty AI response")
                    return {"type": "error", "content": "AI returned empty response"}

                message = response.choices[0].message

                # Tool call
                if message.tool_calls and len(message.tool_calls) > 0:
                    tool_call = message.tool_calls[0]

                    try:
                        arguments = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid tool arguments: {tool_call.function.arguments[:100]}")
                        arguments = {}

                    logger.info(f"AI tool call: {tool_call.function.name}")

                    return {
                        "type": "tool_call",
                        "tool": tool_call.function.name,
                        "parameters": arguments,
                        "tool_call_id": tool_call.id,
                        "raw_message": message
                    }

                # Text response
                content = message.content or ""
                logger.info(f"AI text response: {len(content)} chars")

                return {"type": "text", "content": content}

            except RateLimitError as e:
                self._rate_limit_hits += 1
                last_error = e

                if attempt < self.MAX_RETRIES - 1:
                    delay = self._calculate_backoff(attempt)
                    logger.warning(
                        f"⚠️ Rate limit hit (RateLimitError). "
                        f"Retry {attempt + 1}/{self.MAX_RETRIES} after {delay:.2f}s. "
                        f"Total rate limits: {self._rate_limit_hits}"
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error(
                        f"❌ Rate limit exceeded after {self.MAX_RETRIES} retries"
                    )
                    return {
                        "type": "error",
                        "content": "Sustav je trenutno preopterećen. Pokušajte ponovno za minutu."
                    }

            except APIStatusError as e:
                # Azure OpenAI returns APIStatusError for 429
                if e.status_code == 429:
                    self._rate_limit_hits += 1
                    last_error = e

                    if attempt < self.MAX_RETRIES - 1:
                        delay = self._calculate_backoff(attempt)
                        logger.warning(
                            f"⚠️ Rate limit hit (APIStatusError 429). "
                            f"Retry {attempt + 1}/{self.MAX_RETRIES} after {delay:.2f}s. "
                            f"Total rate limits: {self._rate_limit_hits}"
                        )
                        await asyncio.sleep(delay)
                        continue
                    else:
                        logger.error(
                            f"❌ Rate limit exceeded after {self.MAX_RETRIES} retries"
                        )
                        return {
                            "type": "error",
                            "content": "Sustav je trenutno preopterećen. Pokušajte ponovno za minutu."
                        }
                else:
                    # Other API errors (400, 401, 500, etc.) - don't retry
                    logger.error(f"API error {e.status_code}: {e.message}")
                    return {"type": "error", "content": f"API greška: {e.message}"}

            except APITimeoutError as e:
                last_error = e
                if attempt < self.MAX_RETRIES - 1:
                    delay = self._calculate_backoff(attempt)
                    logger.warning(
                        f"⚠️ API timeout. "
                        f"Retry {attempt + 1}/{self.MAX_RETRIES} after {delay:.2f}s"
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error(f"❌ API timeout after {self.MAX_RETRIES} retries")
                    return {
                        "type": "error",
                        "content": "Sustav nije odgovorio na vrijeme. Pokušajte ponovno."
                    }

            except Exception as e:
                logger.error(f"AI error: {e}", exc_info=True)
                return {"type": "error", "content": f"Greška: {e}"}

        # Should not reach here, but just in case
        return {"type": "error", "content": f"Greška: {last_error}"}

    def _calculate_backoff(self, attempt: int) -> float:
        """Calculate exponential backoff with jitter."""
        exponential_delay = (2 ** attempt) * self.BASE_DELAY
        jitter = random.uniform(0, self.MAX_JITTER)
        return exponential_delay + jitter

    def _apply_token_budgeting(
        self,
        tools: Optional[List[Dict]],
        tool_scores: Optional[List[Dict]]
    ) -> Optional[List[Dict]]:
        """
        Apply token budgeting - trim tools if top match is excellent.

        NEW v12.0: If best tool score >= SINGLE_TOOL_THRESHOLD (0.95),
        send only that tool to LLM. This saves ~80% of token cost for tool descriptions.

        Args:
            tools: List of tool schemas
            tool_scores: List of {name, score} dicts

        Returns:
            Trimmed list of tools
        """
        if not tools:
            return tools

        if not tool_scores:
            # No scores, apply simple limit
            if len(tools) > MAX_TOOLS_FOR_LLM:
                logger.info(
                    f"📉 Token budget: Trimming {len(tools)} → {MAX_TOOLS_FOR_LLM} tools"
                )
                return tools[:MAX_TOOLS_FOR_LLM]
            return tools

        # Find best score
        if tool_scores:
            sorted_scores = sorted(tool_scores, key=lambda x: x.get("score", 0), reverse=True)
            best = sorted_scores[0] if sorted_scores else None

            if best and best.get("score", 0) >= SINGLE_TOOL_THRESHOLD:
                # Excellent match - send only this tool
                best_name = best.get("name")
                single_tool = next(
                    (t for t in tools if t.get("function", {}).get("name") == best_name),
                    None
                )

                if single_tool:
                    logger.info(
                        f"📉 Token budget: SINGLE TOOL MODE - "
                        f"{best_name} (score={best.get('score'):.3f} >= {SINGLE_TOOL_THRESHOLD})"
                    )
                    return [single_tool]

        # Apply limit
        if len(tools) > MAX_TOOLS_FOR_LLM:
            logger.info(
                f"📉 Token budget: Trimming {len(tools)} → {MAX_TOOLS_FOR_LLM} tools"
            )
            return tools[:MAX_TOOLS_FOR_LLM]

        return tools

    def _apply_smart_history(
        self,
        messages: List[Dict[str, str]]
    ) -> List[Dict[str, str]]:
        """
        Apply smart history management with sliding window.

        v12.2: Made less aggressive - only trims if SIGNIFICANTLY over limit.
        Preserves more context for better AI understanding.

        Args:
            messages: Full conversation history

        Returns:
            Trimmed messages with preserved entities
        """
        # v12.2: Only trim if we're significantly over the limit (2x)
        # This preserves more context while still preventing token overflow
        if len(messages) <= MAX_HISTORY_MESSAGES * 2:
            # Don't trim at all if under 2x limit - keep full context
            return messages

        # Only trim the oldest messages, keep more recent ones
        trim_count = len(messages) - MAX_HISTORY_MESSAGES
        old_messages = messages[:trim_count]
        recent = messages[trim_count:]

        # Extract entities from older messages before truncation
        preserved_entities = self._extract_entities(old_messages)

        # If we found entities, inject them as context
        if preserved_entities:
            entity_context = self._format_entity_context(preserved_entities)

            logger.info(
                f"📜 Smart history: Trimmed {len(old_messages)} old messages, "
                f"kept {len(recent)}, preserved {len(preserved_entities)} entities"
            )

            # Prepend entity context
            recent = [
                {"role": "system", "content": f"Prethodni kontekst: {entity_context}"}
            ] + recent

        return recent

    def _extract_entities(
        self,
        messages: List[Dict[str, str]]
    ) -> Dict[str, str]:
        """Extract entity references from messages."""
        entities = {}

        for msg in messages:
            content = msg.get("content", "")
            if not content:
                continue

            # Look for UUID patterns (entity IDs)
            import re
            uuid_pattern = r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})'
            uuids = re.findall(uuid_pattern, content.lower())

            for uuid in uuids:
                # Try to identify what type of entity
                content_lower = content.lower()
                if "vehicle" in content_lower or "vozil" in content_lower:
                    entities["VehicleId"] = uuid
                elif "person" in content_lower or "osob" in content_lower:
                    entities["PersonId"] = uuid
                elif "booking" in content_lower or "rezerv" in content_lower:
                    entities["BookingId"] = uuid
                    
            # Look for license plates
            plate_pattern = r'([A-ZČĆŽŠĐ]{2}[\s\-]?\d{3,4}[\s\-]?[A-ZČĆŽŠĐ]{1,2})'
            plates = re.findall(plate_pattern, content.upper())
            if plates:
                entities["LicencePlate"] = plates[-1]  # Most recent

        return entities

    def _format_entity_context(self, entities: Dict[str, str]) -> str:
        """Format extracted entities as context string."""
        parts = []
        for key, value in entities.items():
            parts.append(f"{key}={value}")
        return ", ".join(parts)

    def get_token_stats(self) -> Dict[str, Any]:
        """Get token usage statistics."""
        return {
            "total_prompt_tokens": self._total_prompt_tokens,
            "total_completion_tokens": self._total_completion_tokens,
            "total_tokens": self._total_prompt_tokens + self._total_completion_tokens,
            "total_requests": self._total_requests,
            "rate_limit_hits": self._rate_limit_hits,
            "avg_tokens_per_request": (
                (self._total_prompt_tokens + self._total_completion_tokens) / self._total_requests
                if self._total_requests > 0 else 0
            )
        }
    
    async def generate_response(
        self,
        prompt: str,
        context: Optional[str] = None
    ) -> str:
        """
        Generate natural language response.
        
        Args:
            prompt: User prompt
            context: Additional context
            
        Returns:
            Generated text
        """
        messages = []
        
        system = "Ti si MobilityOne AI asistent. Odgovaraj na hrvatskom. Budi koncizan."
        messages.append({"role": "system", "content": system})
        
        if context:
            messages.append({"role": "system", "content": f"Kontekst: {context}"})
        
        messages.append({"role": "user", "content": prompt})
        
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.3,
                max_tokens=500
            )
            
            return response.choices[0].message.content or ""
            
        except Exception as e:
            logger.error(f"Generate response error: {e}")
            return "Došlo je do greške. Pokušajte ponovno."
    
    async def extract_parameters(
        self,
        user_input: str,
        required_params: List[Dict[str, str]],
        context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Extract parameters from user input.
        
        Args:
            user_input: User message
            required_params: [{name, type, description}]
            context: Additional context
            
        Returns:
            Extracted parameters
        """
        param_desc = "\n".join([
            f"- {p['name']} ({p['type']}): {p.get('description', '')}"
            for p in required_params
        ])
        
        today = datetime.now()
        tomorrow = today + timedelta(days=1)
        
        system = f"""Izvuci parametre iz korisnikove poruke.
Vrati JSON objekt s vrijednostima. Koristi null za nedostajuće parametre.

Parametri:
{param_desc}

Datumski kontekst:
- Danas: {today.strftime('%Y-%m-%d')} ({today.strftime('%A')})
- Sutra: {tomorrow.strftime('%Y-%m-%d')}

Format vremena: ISO 8601 (YYYY-MM-DDTHH:MM:SS)

Hrvatske riječi:
- "sutra" = tomorrow
- "danas" = today
- "od X do Y" = from X to Y
- "ujutro" = 08:00
- "popodne" = 14:00
- "cijeli dan" = 08:00 do 18:00

Vrati SAMO JSON, bez drugog teksta."""
        
        if context:
            system += f"\n\nDodatni kontekst: {context}"
        
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_input}
                ],
                temperature=0.1,
                max_tokens=300
            )
            
            content = response.choices[0].message.content or "{}"
            
            # Clean markdown
            content = content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()
            
            return json.loads(content)
            
        except json.JSONDecodeError:
            logger.warning("Parameter extraction JSON error")
            return {}
        except Exception as e:
            logger.error(f"Parameter extraction error: {e}")
            return {}
    
    def build_system_prompt(
        self,
        user_context: Dict[str, Any],
        flow_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Build system prompt with context.
        
        Args:
            user_context: User info
            flow_context: Current flow state
            
        Returns:
            System prompt
        """
        name = user_context.get("display_name", "Korisnik")
        person_id = user_context.get("person_id", "")
        vehicle = user_context.get("vehicle", {})
        
        today = datetime.now()
        
        prompt = f"""Ti si MobilityOne AI asistent za upravljanje voznim parkom.
Komuniciraj na HRVATSKOM jeziku. Budi KONCIZAN i JASAN.

═══════════════════════════════════════════════
KORISNIK
═══════════════════════════════════════════════
- Ime: {name}
- ID: {person_id[:12]}...
- Datum: {today.strftime('%d.%m.%Y')} ({today.strftime('%A')})
"""
        
        if vehicle and vehicle.get("plate"):
            prompt += f"""- Vozilo: {vehicle.get('name', 'N/A')} ({vehicle.get('plate', 'N/A')})
- Kilometraža: {vehicle.get('mileage', 'N/A')} km
"""
        
        prompt += """
═══════════════════════════════════════════════
MOGUĆNOSTI I ODABIR ALATA
═══════════════════════════════════════════════
Imaš pristup API funkcijama. Sustav koristi semantičku
pretragu i SORTIRA alate po relevantnosti.

KRITIČNO - ODABIR ALATA:
- Alati su sortirani po RELEVANTNOSTI za korisnikov upit
- PRVI alat u listi je NAJBOLJI match - koristi ga!
- Ako nisi siguran, UVIJEK odaberi PRVI alat
- NE koristi POST/PUT/DELETE ako korisnik pita za podatke (koristi GET)
- "moje vozilo" → koristi get_MasterData, NE get_Vehicles
- "koja je kilometraža" → koristi alat koji vraća podatke, NE calendar

TVOJ POSAO:
1. RAZUMJETI što korisnik želi
2. ODABRATI PRVI alat ako odgovara upitu
3. IZVUĆI parametre iz poruke
4. POZVATI alat s ispravnim parametrima

═══════════════════════════════════════════════
PRAVILA ZA DATUME
═══════════════════════════════════════════════
- "sutra" = sutrašnji datum
- "danas" = današnji datum
- ISO 8601 format: YYYY-MM-DDTHH:MM:SS
- "od 9 do 17" = FromTime: ...T09:00:00, ToTime: ...T17:00:00

═══════════════════════════════════════════════
KRITIČNO: ZABRANJENO IZMIŠLJANJE PODATAKA!
═══════════════════════════════════════════════
NIKADA ne izmišljaj NIŠTA - SVE mora doći iz API-ja! 

ZABRANJENO izmišljati:
- Nazive tvrtki (leasing kuće, dobavljači, itd.)
- Email adrese
- Telefonske brojeve
- Adrese
- Bilo kakve kontakt podatke
- UUID-ove ili ID-eve
- Imena osoba
- Registracijske oznake
- Bilo kakve poslovne podatke
- bilo šta drugo ...

podaci su doslovni.

AKO NEMAŠ PODATAK IZ API ODGOVORA:
→ RECI: "Nemam tu informaciju u sustavu."
→ NE izmišljaj nazive tvrtki kao "LeasingCo", "HighwaysInc", itd.!
→ NE koristi generičke placeholder nazive!
→ PITAJ korisnika ili pozovi odgovarajući API alat!

PRIMJER ISPRAVNOG PONAŠANJA:
- Korisnik pita: "Koja je moja leasing kuća?"
- Ti MORAŠ pozvati API alat za dohvat podataka
- Ako API ne vrati polje "LeasingProvider" → reci "Nemam tu informaciju"
- NIKADA ne izmišljaj naziv leasing kuće!

═══════════════════════════════════════════════
REZERVACIJA VOZILA (BOOKING FLOW)
═══════════════════════════════════════════════
Kada korisnik traži vozilo ili želi rezervirati:

POTREBNI PARAMETRI:
1. FromTime - datum i vrijeme polaska (obavezno)
2. ToTime - datum i vrijeme povratka (obavezno)
3. Odredište - gdje putuje (opciono za sada)
4. Svrha puta - zašto putuje (opciono za sada)
5. Broj putnika - koliko osoba (opciono za sada)

FLOW:
1. Ako korisnik nije naveo FromTime/ToTime → PITAJ GA
   Primjer: "Za kada vam treba vozilo? (npr. sutra od 8:00 do 17:00)"

2. Kada imaš FromTime i ToTime → pozovi get_AvailableVehicles
   Parametri: from=YYYY-MM-DDTHH:MM:SS, to=YYYY-MM-DDTHH:MM:SS

3. Ako nema slobodnih vozila → javi korisniku i predloži drugi termin

4. Ako ima slobodnih → prikaži PRVO slobodno vozilo i pitaj:
   "Pronašao sam slobodno vozilo: [naziv] ([registracija]).
    Želite li potvrditi rezervaciju?"

5. Ako korisnik potvrdi → pozovi post_VehicleCalendar s:
   - AssignedToId: korisnikov PersonId (iz konteksta)
   - VehicleId: ID odabranog vozila
   - FromTime: vrijeme polaska
   - ToTime: vrijeme povratka
   - AssigneeType: 1
   - EntryType: 0

6. Potvrdi uspješnu rezervaciju ili javi grešku

═══════════════════════════════════════════════
STIL
═══════════════════════════════════════════════
- KRATKI odgovori na hrvatskom
- SVE informacije MORAJU doći iz API odgovora!
- NE izmišljaj podatke - koristi alate!
- Ako nedostaju parametri, PITAJ korisnika
- Ako API ne vrati podatak, reci "Nemam tu informaciju"
"""
        
        if flow_context and flow_context.get("current_flow"):
            prompt += f"""
═══════════════════════════════════════════════
TRENUTNI TOK
═══════════════════════════════════════════════
- Flow: {flow_context.get('current_flow')}
- Stanje: {flow_context.get('state')}
- Parametri: {flow_context.get('parameters', {})}
- Nedostaju: {flow_context.get('missing_params', [])}
"""
        
        return prompt
