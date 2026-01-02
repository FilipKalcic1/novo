
import asyncio
import json
import logging
import random
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple

from openai import AsyncAzureOpenAI, RateLimitError, APIStatusError, APITimeoutError

from config import get_settings
from services.sanitizer import sanitize
from services.patterns import PatternRegistry

logger = logging.getLogger(__name__)
settings = get_settings()

try:
    import tiktoken
except ImportError:
    tiktoken = None


# Token budgeting constants
SINGLE_TOOL_THRESHOLD = 0.98
MAX_TOOLS_FOR_LLM = 10
MAX_HISTORY_MESSAGES = 20
MAX_TOKEN_LIMIT = 8000

# LOW FIX v12.2: Token counting overhead constants
MESSAGE_TOKEN_OVERHEAD = 3  # Tokens added per message (OpenAI format overhead)
FINAL_TOKEN_OVERHEAD = 3    # Final overhead added to total count

# System prompts
DEFAULT_SYSTEM_PROMPT = "Ti si MobilityOne AI asistent. Odgovaraj na hrvatskom. Budi koncizan."
RATE_LIMIT_ERROR_MSG = "Sustav je trenutno preopterećen. Pokušajte ponovno za minutu."
TIMEOUT_ERROR_MSG = "Sustav nije odgovorio na vrijeme. Pokušajte ponovno." 



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

        self.tokenizer = None
        if tiktoken:
            try:
                self.tokenizer = tiktoken.get_encoding("cl100k_base")
            except Exception as e:
                logger.warning(f"Tokenizer initialization error: {e}")
                logger.info("Falling back to approximate token counting.")
    
    def _count_tokens(self, messages: List[Dict[str, str]]) -> int:
        """Azure-safe token counting."""
        
        if not self.tokenizer:
            # MEDIUM FIX v12.2: Improved fallback for Croatian language
            # Croatian avg word ~6 chars, ~1.3 tokens/word
            # Approx: chars ÷ 6 × 1.3 = chars ÷ 4.6
            total_chars = sum(len(m.get("content", "")) for m in messages)
            # Add per-message overhead
            return int(total_chars / 4.6) + len(messages) * MESSAGE_TOKEN_OVERHEAD

        num_tokens = 0
        for message in messages:
            num_tokens += MESSAGE_TOKEN_OVERHEAD
            for key, value in message.items():
                if value:
                    num_tokens += len(self.tokenizer.encode(str(value)))

        num_tokens += FINAL_TOKEN_OVERHEAD
        return num_tokens


            
    async def analyze(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict]] = None,
        system_prompt: Optional[str] = None,
        forced_tool: Optional[str] = None,
        tool_scores: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:

        # NEW v12.0: Apply Smart History (sliding window)
        filtered_conversation = self._apply_smart_history(messages)

        # Build final message list with system prompt first
        final_messages = []

        # CRITICAL FIX v12.2: Prevent duplicate system prompts
        # _apply_smart_history may already include a system message
        has_system_in_filtered = (
            filtered_conversation and
            len(filtered_conversation) > 0 and
            filtered_conversation[0].get("role") == "system"
        )

        if system_prompt and not has_system_in_filtered:
            final_messages.append({"role": "system", "content": system_prompt})

        final_messages.extend(filtered_conversation)

        # NEW v12.0: Apply Token Budgeting (trim tools if top match is excellent)
        # CRITICAL FIX v15.1: Pass forced_tool to prevent mismatch
        trimmed_tools = self._apply_token_budgeting(tools, tool_scores, forced_tool)

        call_args = {
            "model": self.model,
            "messages": final_messages,
            "temperature": settings.AI_TEMPERATURE,
            "max_tokens": settings.AI_MAX_TOKENS
        }

        if trimmed_tools:
            call_args["tools"] = trimmed_tools

            # ACTION-FIRST PROTOCOL: Force specific tool if similarity >= ACTION_THRESHOLD
            # CRITICAL FIX v15.1: Validate forced_tool is actually in trimmed_tools
            if forced_tool:
                # Check if forced_tool exists in trimmed_tools
                tool_names_in_list = [t.get("function", {}).get("name") for t in trimmed_tools]

                if forced_tool in tool_names_in_list:
                    call_args["tool_choice"] = {
                        "type": "function",
                        "function": {"name": forced_tool}
                    }
                    logger.info(f"Forced tool call: {forced_tool} (similarity >= {settings.ACTION_THRESHOLD})")
                else:
                    # Forced tool not in trimmed list - fall back to auto
                    logger.warning(
                        f"Forced tool '{forced_tool}' not in trimmed tools list "
                        f"({tool_names_in_list}). Falling back to 'auto' selection."
                    )
                    call_args["tool_choice"] = "auto"
            else:
                call_args["tool_choice"] = "auto"

        # NEW v12.0: Retry with exponential backoff
        last_error = None

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await self.client.chat.completions.create(**call_args)
                self._total_requests += 1

                if hasattr(response, 'usage') and response.usage:
                    self._total_prompt_tokens += response.usage.prompt_tokens
                    self._total_completion_tokens += response.usage.completion_tokens

                    logger.debug(
                        f"Tokens: prompt={response.usage.prompt_tokens}, "
                        f"completion={response.usage.completion_tokens}, "
                        f"total={self._total_prompt_tokens + self._total_completion_tokens}"
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

                    logger.debug(f"Tool call: {tool_call.function.name}")

                    return {
                        "type": "tool_call",
                        "tool": tool_call.function.name,
                        "parameters": arguments,
                        "tool_call_id": tool_call.id,
                        "raw_message": message
                    }

                content = message.content or ""
                logger.debug(f"Text response: {len(content)} chars")

                return {"type": "text", "content": content}

            except RateLimitError as e:
                last_error = e
                result = await self._handle_rate_limit(attempt, "RateLimitError")
                if result:
                    return result
                continue

            except APIStatusError as e:
                if e.status_code == 429:
                    last_error = e
                    result = await self._handle_rate_limit(attempt, "APIStatusError 429")
                    if result:
                        return result
                    continue

                logger.error(f"API error {e.status_code}: {e.message}")
                return {"type": "error", "content": f"API greška: {e.message}"}

            except APITimeoutError as e:
                last_error = e
                result = await self._handle_timeout(attempt)
                if result:
                    return result
                continue

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

    async def _handle_rate_limit(self, attempt: int, error_type: str) -> Optional[Dict[str, Any]]:
        """Handle rate limit errors with retry logic."""
        self._rate_limit_hits += 1

        if attempt < self.MAX_RETRIES - 1:
            delay = self._calculate_backoff(attempt)
            logger.warning(
                f"Rate limit hit ({error_type}). "
                f"Retry {attempt + 1}/{self.MAX_RETRIES} after {delay:.2f}s. "
                f"Total: {self._rate_limit_hits}"
            )
            await asyncio.sleep(delay)
            return None

        logger.error(f"Rate limit exceeded after {self.MAX_RETRIES} retries")
        return {"type": "error", "content": RATE_LIMIT_ERROR_MSG}

    async def _handle_timeout(self, attempt: int) -> Optional[Dict[str, Any]]:
        """Handle timeout errors with retry logic."""
        if attempt < self.MAX_RETRIES - 1:
            delay = self._calculate_backoff(attempt)
            logger.warning(f"API timeout. Retry {attempt + 1}/{self.MAX_RETRIES} after {delay:.2f}s")
            await asyncio.sleep(delay)
            return None

        logger.error(f"API timeout after {self.MAX_RETRIES} retries")
        return {"type": "error", "content": TIMEOUT_ERROR_MSG}

    def _apply_token_budgeting(
        self,
        tools: Optional[List[Dict]],
        tool_scores: Optional[List[Dict]],
        forced_tool: Optional[str] = None
    ) -> Optional[List[Dict]]:
        """
        Apply token budgeting - trim tools if top match is excellent.

        NEW v12.0: If best tool score >= SINGLE_TOOL_THRESHOLD (0.98),
        send only that tool to LLM. This saves ~80% of token cost for tool descriptions.

        CRITICAL FIX v15.1: If forced_tool is specified and differs from best_match,
        don't apply SINGLE TOOL MODE to ensure forced_tool is in the list.

        CRITICAL REQUIREMENTS:
        1. tools and tool_scores MUST be in the SAME ORDER (sorted by score DESC)
        2. tool_scores MUST contain 'name' and 'score' fields
        3. tools MUST be OpenAI tool schemas with structure: {"type": "function", "function": {"name": "..."}}

        Args:
            tools: List of tool schemas (sorted by score DESC)
            tool_scores: List of {name, score, ...} dicts (sorted by score DESC)
            forced_tool: Optional tool name that will be forced in execution

        Returns:
            Trimmed list of tools (maintains sort order)
        """
        if not tools:
            return tools

        if not tool_scores:
            # No scores, apply simple limit
            if len(tools) > MAX_TOOLS_FOR_LLM:
                logger.info(
                    f" Token budget: Trimming {len(tools)} → {MAX_TOOLS_FOR_LLM} tools"
                )
                return tools[:MAX_TOOLS_FOR_LLM]
            return tools

        # VALIDATION: Ensure tools and tool_scores are aligned
        # MEDIUM FIX v12.2: Return early to prevent misaligned tool selection
        if len(tools) != len(tool_scores):
            logger.error(
                f"Token budgeting: tools count ({len(tools)}) != "
                f"tool_scores count ({len(tool_scores)}). "
                f"Returning tools without budgeting to avoid mismatch."
            )
            return tools  # STOP processing - don't continue with misaligned data

        # Find best score (tool_scores should already be sorted DESC by message_engine)
        best = tool_scores[0] if tool_scores else None

        if best and best.get("score", 0) >= SINGLE_TOOL_THRESHOLD:
            best_name = best.get("name")

            # CRITICAL FIX v15.1: Check if forced_tool conflicts with best match
            if forced_tool and forced_tool != best_name:
                logger.info(
                    f" Token budget: Skipping SINGLE TOOL MODE - forced_tool '{forced_tool}' "
                    f"differs from best_match '{best_name}' (score={best.get('score'):.3f})"
                )
                # Don't apply SINGLE TOOL MODE - let it fall through to normal trimming
            else:
                # Excellent match - send only this tool
                # (or forced_tool matches best, so it's safe)
                single_tool = next(
                    (t for t in tools if t.get("function", {}).get("name") == best_name),
                    None
                )

                if single_tool:
                    logger.info(
                        f" Token budget: SINGLE TOOL MODE - "
                        f"{best_name} (score={best.get('score'):.3f} >= {SINGLE_TOOL_THRESHOLD})"
                    )
                    return [single_tool]
                else:
                    logger.error(
                        f" Token budgeting: Best tool '{best_name}' not found in tools list! "
                        f"Available tools: {[t.get('function', {}).get('name') for t in tools]}"
                    )

        # Apply limit (tools already sorted by score DESC)
        # CRITICAL FIX v15.1: Ensure forced_tool is included if specified
        if forced_tool:
            # Check if forced_tool is in the list
            tool_names = [t.get("function", {}).get("name") for t in tools]

            if forced_tool in tool_names:
                forced_tool_obj = next(
                    (t for t in tools if t.get("function", {}).get("name") == forced_tool),
                    None
                )

                if forced_tool_obj:
                    # Ensure forced_tool is in the trimmed list
                    trimmed = tools[:MAX_TOOLS_FOR_LLM]

                    if forced_tool_obj not in trimmed:
                        # Replace last tool with forced_tool to ensure it's included
                        logger.info(
                            f" Token budget: Adding forced_tool '{forced_tool}' to trimmed list"
                        )
                        trimmed = trimmed[:-1] + [forced_tool_obj]

                    return trimmed
            else:
                logger.warning(
                    f"Token budgeting: forced_tool '{forced_tool}' not found in tools list. "
                    f"Available: {tool_names}"
                )

        if len(tools) > MAX_TOOLS_FOR_LLM:
            logger.info(
                f"Token budget: Trimming {len(tools)} → {MAX_TOOLS_FOR_LLM} tools "
                f"(keeping top {MAX_TOOLS_FOR_LLM} by score)"
            )
            return tools[:MAX_TOOLS_FOR_LLM]

        return tools

    
    def _apply_smart_history(
        self,
        messages: List[Dict[str, str]]
    ) -> List[Dict[str, str]]:

        # 1. IZDVAJANJE SYSTEM PROMPTA

        system_message = None
        if messages and messages[0]["role"] == "system":
            system_message = messages[0]
            conversation = messages[1:] # Ostatak razgovora
        else:
            conversation = messages

        # 2. PROVJERA TOKENA (Preciznije od broja poruka)

        current_tokens = self._count_tokens(messages)
        
        # Ako smo ispod limita, ne diraj ništa
        if current_tokens <= MAX_TOKEN_LIMIT:
            return messages

        # 3. REZANJE KONTEKSTA
        split_index = max(0, len(conversation) - MAX_HISTORY_MESSAGES)
        
        to_summarize = conversation[:split_index]
        recent_history = conversation[split_index:]

        # 4. SAŽIMANJE (Bolje od samih entiteta)
        if to_summarize:

            summary_text = self._summarize_conversation(to_summarize)
            
            # Ubacujemo sažetak KAO SYSTEM poruku, ali ODMAH NAKON glavnog system prompta
            context_message = {
                "role": "system", 
                "content": f"Sažetak prethodnog razgovora: {summary_text}"
            }
            
            # 5. REKONSTRUKCIJA
            final_messages = []
            if system_message:
                final_messages.append(system_message)
            
            final_messages.append(context_message)
            final_messages.extend(recent_history)

            # CRITICAL FIX v12.2: Re-check token count after summarization
            # Summary might still push us over the limit
            final_tokens = self._count_tokens(final_messages)
            if final_tokens > MAX_TOKEN_LIMIT:
                logger.warning(
                    f"Summary still over limit ({final_tokens} > {MAX_TOKEN_LIMIT}). "
                    f"Trimming recent_history further (20 → 10 messages)"
                )
                # Fallback: Keep only last 10 messages instead of 20
                recent_history = recent_history[-10:]
                final_messages = []
                if system_message:
                    final_messages.append(system_message)
                final_messages.append(context_message)
                final_messages.extend(recent_history)

            return final_messages

        return messages

    def _extract_entities(self, messages: List[Dict[str, str]]) -> Dict[str, List[str]]:
        """
        Extract entity references from messages.

        CRITICAL FIX v12.2: Changed to store lists of entities instead of single values.
        Prevents data loss when multiple UUIDs/plates are mentioned.

        Returns:
            Dict mapping entity types to lists of values
        """
        # Initialize with empty lists to prevent data loss
        entities = {
            "VehicleId": [],
            "PersonId": [],
            "BookingId": [],
            "LicencePlate": []
        }

        for msg in messages:
            content = msg.get("content", "")
            if not content:
                continue

            # PERFORMANCE FIX: Compute content_lower once per message
            content_lower = content.lower()

            # Use centralized PatternRegistry for consistent pattern matching
            uuids = PatternRegistry.find_uuids(content)
            for uuid in uuids:
                # CRITICAL FIX: Append to list instead of overwriting
                if "vehicle" in content_lower or "vozil" in content_lower:
                    entities["VehicleId"].append(uuid)
                elif "person" in content_lower or "osob" in content_lower:
                    entities["PersonId"].append(uuid)
                elif "booking" in content_lower or "rezerv" in content_lower:
                    entities["BookingId"].append(uuid)

            # Use centralized PatternRegistry for Croatian plates
            plates = PatternRegistry.find_plates(content)
            # CRITICAL FIX: Append all plates, not just last one
            entities["LicencePlate"].extend(plates)

        return entities

    def _format_entity_context(self, entities: Dict[str, List[str]]) -> str:
        """
        Format extracted entities as context string.

        CRITICAL FIX v12.2: Updated to handle lists of entities.

        Args:
            entities: Dict mapping entity types to lists of values

        Returns:
            Formatted string like "VehicleId=uuid1,uuid2, PersonId=uuid3"
        """
        parts = []
        for key, values in entities.items():
            if values:  # Only include non-empty lists
                parts.append(f"{key}={','.join(values)}")
        return ", ".join(parts)

    def _summarize_conversation(self, messages: List[Dict[str, str]]) -> str:
        """Summarize old conversation messages into a concise context."""
        entities = self._extract_entities(messages)
        summary_parts = []

        if entities:
            summary_parts.append(f"Ranije entiteti: {self._format_entity_context(entities)}")

        role_counts = Counter(m.get("role") for m in messages)
        # CRITICAL FIX v12.2: Use .get() to prevent KeyError if role is missing
        summary_parts.append(
            f"Prethodnih {len(messages)} poruka "
            f"({role_counts.get('user', 0)} user, {role_counts.get('assistant', 0)} assistant, {role_counts.get('tool', 0)} tool calls)"
        )

        return ". ".join(summary_parts)

    # TODO: Expose via /admin/token-stats endpoint for monitoring
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

⚠️ KRITIČNO - NE IZMIŠLJAJ DATUME:
- AKO korisnik NIJE NAVEO datum/vrijeme - vrati null!
- NE pretpostavljaj današnji datum!
- NE dodavaj default vrijednosti za from/to/FromTime/ToTime!
- Samo ako korisnik EKSPLICITNO kaže "danas" ili "sutra" - onda koristi datum

Parametri:
{param_desc}

Datumski kontekst (KORISTI SAMO ako korisnik EKSPLICITNO spomene):
- Danas: {today.strftime('%Y-%m-%d')} ({today.strftime('%A')})
- Sutra: {tomorrow.strftime('%Y-%m-%d')}

Format vremena: ISO 8601 (YYYY-MM-DDTHH:MM:SS)

Hrvatske riječi za vrijeme:
- "sutra" = tomorrow (SAMO ako korisnik kaže "sutra")
- "danas" = today (SAMO ako korisnik kaže "danas")
- "prekosutra" = day after tomorrow (+2 dana od danas)
- "od X do Y" = from X to Y
- "ujutro" = 08:00
- "popodne" = 14:00
- "navečer" = 18:00
- "cijeli dan" = 08:00 do 18:00

VAŽNO: Ako korisnik daje samo vrijeme/datum kao odgovor (npr. "17:00" ili "prekosutra 9:00"),
to je vjerojatno odgovor na prethodno pitanje. Koristi taj datum/vrijeme za traženi parametar.

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
        -ime automobila/vozila
        -registracija vozila
        -datum istijeka registracije
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

        !!! KRITIČNO - ZABRANJENO IZMIŠLJANJE !!!
        - NIKADA ne izmišljaj broj slobodnih vozila (npr. "3 vozila")
        - NIKADA ne izmišljaj registracijske oznake (npr. "ZG-1234-AB")
        - NIKADA ne generiraj odgovor o vozilima BEZ poziva get_AvailableVehicles!
        - Broj vozila MORA biti len(API_response.Data) - stvarni broj!

        PRIMJER GREŠKE (ZABRANJENO):
        - Korisnik: "Trebam vozilo sutra"
        - Ti: "Pronašao sam 3 slobodna vozila..." ← KRIVO! Nisi pozvao API!

        ISPRAVNO:
        - Korisnik: "Trebam vozilo sutra"
        - Ti: Prvo pozovi get_AvailableVehicles(from=..., to=...)
        - Tek nakon što dobiješ odgovor, reci: "Pronašao sam {len(Data)} vozila..."

        POTREBNI PARAMETRI:
        1. FromTime - datum i vrijeme polaska (obavezno)
        2. ToTime - datum i vrijeme povratka (obavezno)

        FLOW:
        1. Ako korisnik nije naveo FromTime/ToTime → PITAJ GA
        Primjer: "Za kada vam treba vozilo? (npr. sutra od 8:00 do 17:00)"

        2. Kada imaš FromTime i ToTime → OBAVEZNO pozovi get_AvailableVehicles
        Parametri: from=YYYY-MM-DDTHH:MM:SS, to=YYYY-MM-DDTHH:MM:SS

        3. Ako nema slobodnih vozila → javi korisniku i predloži drugi termin

        4. Ako ima slobodnih → prikaži PRVO slobodno vozilo i pitaj:
        "Pronašao sam slobodno vozilo: [naziv] ([registracija]).
            Želite li potvrditi rezervaciju?"
        Napomena: [naziv] i [registracija] MORAJU biti iz API odgovora!

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
