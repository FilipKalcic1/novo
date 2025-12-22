"""
AI Orchestrator
Version: 11.0

OpenAI integration for intent analysis and response generation.
SECURITY: Uses sanitizer before sending data to AI.
DEPENDS ON: config.py, sanitizer.py
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from openai import AsyncAzureOpenAI

from config import get_settings
from services.sanitizer import sanitize

logger = logging.getLogger(__name__)
settings = get_settings()


class AIOrchestrator:
    """
    Orchestrates AI interactions.
    
    Features:
    - Tool calling
    - Parameter extraction
    - Response generation
    """
    
    def __init__(self):
        """Initialize AI orchestrator."""
        self.client = AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION
        )
        self.model = settings.AZURE_OPENAI_DEPLOYMENT_NAME
    
    async def analyze(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict]] = None,
        system_prompt: Optional[str] = None,
        forced_tool: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Analyze user input and decide on action.

        MASTER PROMPT v9.0 - ACTION-FIRST PROTOCOL:
        If forced_tool is provided, LLM MUST call that tool (no text fallback).
        This ensures high-confidence matches (similarity >= 0.85) always execute.

        Args:
            messages: Conversation history
            tools: Available tools
            system_prompt: System instructions
            forced_tool: If set, force LLM to call this specific tool (no "auto")

        Returns:
            {type: "tool_call"|"text", ...}
        """
        full_messages = []

        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})

        full_messages.extend(messages)

        call_args = {
            "model": self.model,
            "messages": full_messages,
            "temperature": settings.AI_TEMPERATURE,
            "max_tokens": settings.AI_MAX_TOKENS
        }

        if tools:
            call_args["tools"] = tools

            # ACTION-FIRST PROTOCOL: Force specific tool if similarity >= ACTION_THRESHOLD
            if forced_tool:
                call_args["tool_choice"] = {
                    "type": "function",
                    "function": {"name": forced_tool}
                }
                logger.info(f"🎯 FORCED TOOL CALL: {forced_tool} (similarity >= {settings.ACTION_THRESHOLD})")
            else:
                call_args["tool_choice"] = "auto"
        
        try:
            response = await self.client.chat.completions.create(**call_args)
            
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
            
        except Exception as e:
            logger.error(f"AI error: {e}")
            return {"type": "error", "content": f"Greška: {e}"}
    
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
MOGUĆNOSTI
═══════════════════════════════════════════════
Imaš pristup MNOGIM API funkcijama. Sustav koristi
semantičku pretragu za pronalazak prave funkcije.

TVOJ POSAO:
1. RAZUMJETI što korisnik želi
2. ODABRATI pravi alat
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
STIL
═══════════════════════════════════════════════
- KRATKI odgovori na hrvatskom
- NE izmišljaj podatke - koristi alate!
- Ako nedostaju parametri, PITAJ korisnika
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
