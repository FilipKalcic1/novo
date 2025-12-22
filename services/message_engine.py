"""
Message Engine
Version: 11.0

Main message processing orchestrator.

CRITICAL FIXES:
1. Uses Redis-backed ConversationManager (survives restarts)
2. Passes error feedback to AI for self-correction
3. Better tool execution loop with error recovery
"""

import json
import logging
from typing import Dict, Any, Optional, List

from config import get_settings
from services.conversation_manager import ConversationManager, ConversationState
from services.tool_executor import ToolExecutor
from services.ai_orchestrator import AIOrchestrator
from services.response_formatter import ResponseFormatter
from services.user_service import UserService

logger = logging.getLogger(__name__)
settings = get_settings()


class MessageEngine:
    """
    Main message processing engine.
    
    Coordinates:
    - User identification
    - Conversation state (Redis-backed)
    - AI interactions with error feedback
    - Tool execution with validation
    """
    
    MAX_ITERATIONS = 6
    
    def __init__(
        self,
        gateway,
        registry,
        context_service,
        queue_service,
        cache_service,
        db_session
    ):
        """Initialize engine with all services."""
        self.gateway = gateway
        self.registry = registry
        self.executor = ToolExecutor(gateway)  # CRITICAL FIX: ToolExecutor only takes gateway
        self.context = context_service
        self.queue = queue_service
        self.cache = cache_service
        self.db = db_session
        self.redis = context_service.redis if context_service else None
        
        self.ai = AIOrchestrator()
        self.formatter = ResponseFormatter()
    
    async def process(
        self,
        sender: str,
        text: str,
        message_id: Optional[str] = None
    ) -> str:
        """
        Process incoming message.
        
        Args:
            sender: User phone number
            text: Message text
            message_id: Optional message ID
            
        Returns:
            Response text
        """
        logger.info(f"📨 Processing: {sender[-4:]} - {text[:50]}")
        
        try:
            # 1. Identify user
            user_context = await self._identify_user(sender)
            
            if not user_context:
                return (
                    "⛔ Vaš broj nije pronađen u sustavu MobilityOne.\n"
                    "Molimo kontaktirajte administratora."
                )
            
            # 2. Load conversation state from Redis
            conv_manager = await ConversationManager.load_for_user(sender, self.redis)
            
            # 3. Check timeout
            if conv_manager.is_timed_out():
                await conv_manager.reset()
            
            # 4. Add to history
            await self.context.add_message(sender, "user", text)
            
            # 5. Check if new user - send greeting
            if user_context.get("is_new"):
                greeting = await self._build_greeting(user_context)
                await self.context.add_message(sender, "assistant", greeting)
                
                # Still process their message
                response = await self._process_with_state(
                    sender, text, user_context, conv_manager
                )
                
                # Combine greeting with response
                full_response = f"{greeting}\n\n---\n\n{response}"
                await self.context.add_message(sender, "assistant", response)
                return full_response
            
            # 6. Process based on state
            response = await self._process_with_state(
                sender, text, user_context, conv_manager
            )
            
            # 7. Save response to history
            await self.context.add_message(sender, "assistant", response)
            
            return response
            
        except Exception as e:
            logger.error(f"❌ Processing error: {e}", exc_info=True)
            return "Došlo je do greške. Molimo pokušajte ponovno."
    
    async def _identify_user(self, phone: str) -> Optional[Dict[str, Any]]:
        """Identify user and build context."""
        user_service = UserService(self.db, self.gateway, self.cache)
        
        user = await user_service.get_active_identity(phone)
        
        if user:
            ctx = await user_service.build_context(user.api_identity, phone)
            ctx["display_name"] = user.display_name
            ctx["is_new"] = False
            return ctx
        
        # Try auto-onboard
        result = await user_service.try_auto_onboard(phone)
        
        if result:
            display_name, vehicle_info = result
            user = await user_service.get_active_identity(phone)
            
            if user:
                ctx = await user_service.build_context(user.api_identity, phone)
                ctx["display_name"] = display_name
                ctx["is_new"] = True
                ctx["vehicle_info"] = vehicle_info  # Human-readable string
                return ctx
        
        return None
    
    async def _build_greeting(self, user_context: Dict[str, Any]) -> str:
        """
        Build personalized greeting for new user.
        
        Includes:
        - User name
        - Assigned vehicle info
        - Offer to help with booking if no vehicle
        """
        name = user_context.get("display_name", "")
        vehicle = user_context.get("vehicle", {})
        vehicle_info = user_context.get("vehicle_info", "")
        
        greeting = f"👋 Pozdrav {name}!\n\n"
        greeting += "Ja sam MobilityOne AI asistent i mogu vam pomoći s upravljanjem vozilom.\n\n"
        
        if vehicle.get("plate"):
            plate = vehicle.get("plate")
            v_name = vehicle.get("name", "vozilo")
            mileage = vehicle.get("mileage", "N/A")
            
            greeting += f"🚗 Vidim da vam je dodijeljeno vozilo:\n"
            greeting += f"   **{v_name}** ({plate})\n"
            greeting += f"   Kilometraža: {mileage} km\n\n"
            greeting += "Kako vam mogu pomoći?\n"
            greeting += "• Unos kilometraže\n"
            greeting += "• Prijava kvara\n"
            greeting += "• Rezervacija vozila\n"
            greeting += "• Pitanja o vozilu"
        elif vehicle_info and "nema" not in vehicle_info.lower():
            greeting += f"🚗 {vehicle_info}\n\n"
            greeting += "Kako vam mogu pomoći?"
        else:
            greeting += "📋 Trenutno nemate dodijeljeno vozilo.\n\n"
            greeting += "Želite li rezervirati vozilo? Recite mi:\n"
            greeting += "• Za koji period (npr. 'sutra od 8 do 17')\n"
            greeting += "• Ili samo recite 'Trebam vozilo' pa ćemo dalje"
        
        return greeting
    
    async def _process_with_state(
        self,
        sender: str,
        text: str,
        user_context: Dict[str, Any],
        conv_manager: ConversationManager
    ) -> str:
        """Process message based on conversation state."""
        state = conv_manager.get_state()
        
        if state == ConversationState.SELECTING_ITEM:
            return await self._handle_selection(sender, text, user_context, conv_manager)
        
        if state == ConversationState.CONFIRMING:
            return await self._handle_confirmation(sender, text, user_context, conv_manager)
        
        if state == ConversationState.GATHERING_PARAMS:
            return await self._handle_gathering(sender, text, user_context, conv_manager)
        
        # IDLE - new request
        return await self._handle_new_request(sender, text, user_context, conv_manager)
    
    async def _handle_new_request(
        self,
        sender: str,
        text: str,
        user_context: Dict[str, Any],
        conv_manager: ConversationManager
    ) -> str:
        """
        Handle new request with AI tool calling.

        MASTER PROMPT v9.0 - ACTION-FIRST PROTOCOL:
        If best tool has similarity >= ACTION_THRESHOLD (0.85), force tool execution.
        Bot is FORBIDDEN from sending generic text response in this case.
        """
        # Get history
        history = await self.context.get_recent_messages(sender)

        # Build messages for AI
        messages = history.copy()
        messages.append({"role": "user", "content": text})

        # Get relevant tools WITH SCORES for ACTION-FIRST decision
        tools_with_scores = await self.registry.find_relevant_tools_with_scores(
            text, top_k=5
        )

        # Extract tools and determine if forced execution is needed
        tools = [t["schema"] for t in tools_with_scores]
        forced_tool = None

        if tools_with_scores:
            # Find best match
            best_match = max(tools_with_scores, key=lambda t: t["score"])
            best_score = best_match["score"]
            best_tool_name = best_match["name"]

            tool_names = [t["name"] for t in tools_with_scores]
            logger.info(f"🔧 Available tools: {tool_names}")
            logger.info(f"🎯 Best match: {best_tool_name} (score={best_score:.3f})")

            # ACTION-FIRST PROTOCOL: Force tool call if score >= ACTION_THRESHOLD
            if best_score >= settings.ACTION_THRESHOLD:
                forced_tool = best_tool_name
                logger.info(
                    f"⚡ ACTION-FIRST: Forcing {forced_tool} "
                    f"(score {best_score:.3f} >= threshold {settings.ACTION_THRESHOLD})"
                )

        # Build system prompt
        system_prompt = self.ai.build_system_prompt(
            user_context,
            conv_manager.to_dict() if conv_manager.is_in_flow() else None
        )

        # AI iteration loop with error feedback
        current_messages = messages.copy()

        for iteration in range(self.MAX_ITERATIONS):
            logger.debug(f"AI iteration {iteration + 1}/{self.MAX_ITERATIONS}")

            # Pass forced_tool on first iteration only (subsequent iterations are corrections)
            current_forced = forced_tool if iteration == 0 else None

            result = await self.ai.analyze(
                messages=current_messages,
                tools=tools,
                system_prompt=system_prompt,
                forced_tool=current_forced
            )
            
            if result.get("type") == "error":
                return result.get("content", "Greška u AI obradi.")
            
            if result.get("type") == "text":
                return result.get("content", "")
            
            if result.get("type") == "tool_call":
                tool_response = await self._execute_tool_call(
                    result, user_context, conv_manager, sender
                )
                
                # Final response ready
                if tool_response.get("final_response"):
                    return tool_response["final_response"]
                
                # Needs user input (selection, confirmation)
                if tool_response.get("needs_input"):
                    return tool_response.get("prompt", "")
                
                # Add tool call and result to conversation
                tool_result_content = tool_response.get("data", {})
                
                # Include AI feedback if there was an error
                if not tool_response.get("success", True):
                    ai_feedback = tool_response.get("ai_feedback", "")
                    tool_result_content = {
                        "error": True,
                        "message": tool_response.get("error", "Unknown error"),
                        "ai_feedback": ai_feedback
                    }
                
                current_messages.append({
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": result["tool_call_id"],
                        "type": "function",
                        "function": {
                            "name": result["tool"],
                            "arguments": json.dumps(result["parameters"])
                        }
                    }]
                })
                
                current_messages.append({
                    "role": "tool",
                    "tool_call_id": result["tool_call_id"],
                    "content": json.dumps(tool_result_content)
                })
        
        return "Nisam uspio obraditi zahtjev. Pokušajte drugačije formulirati."
    
    async def _execute_tool_call(
        self,
        result: Dict[str, Any],
        user_context: Dict[str, Any],
        conv_manager: ConversationManager,
        sender: str
    ) -> Dict[str, Any]:
        """
        Execute tool call with proper error handling and AI feedback.

        Args:
            result: Tool call result from AI containing tool name and parameters
            user_context: User context with tenant_id, person_id, etc.
            conv_manager: Conversation state manager
            sender: User phone number

        Returns:
            Dict with:
                - success: bool
                - data: Tool execution result
                - ai_feedback: Error feedback for AI self-correction
                - final_response: Human-readable response (optional)
                - needs_input: True if requires user selection/confirmation (optional)
        """
        tool_name = result["tool"]
        parameters = result["parameters"]

        logger.info(f"🔧 Executing: {tool_name} with params: {list(parameters.keys())}")

        # Get tool definition from registry
        tool = self.registry.get_tool(tool_name)

        if not tool:
            logger.error(f"❌ Tool not found: {tool_name}")
            return {
                "success": False,
                "data": {"error": f"Tool {tool_name} not found"},
                "ai_feedback": f"Tool '{tool_name}' does not exist. Use a different tool from the available list.",
                "final_response": (
                    f"⚠️ Tehnički problem - alat '{tool_name}' nije pronađen u sustavu.\n"
                    f"Pokušajte reformulirati zahtjev."
                )
            }

        # CRITICAL FIX: Access Pydantic property directly, not via .get()
        method = tool.method  # UnifiedToolDefinition has 'method' property
        
        # Availability check (returns list for selection)
        if "available" in tool_name.lower() or "calendar" in tool_name.lower():
            if method == "GET" and "vehicle" in tool_name.lower():
                return await self._handle_availability(tool_name, parameters, user_context, conv_manager)
        
        # POST/PUT may need confirmation for critical operations
        if method in ("POST", "PUT", "PATCH"):
            if self._requires_confirmation(tool_name):
                return await self._request_confirmation(tool_name, parameters, user_context, conv_manager)
        
        # Direct execution
        try:
            # CRITICAL FIX: Create ToolExecutionContext and call with correct signature
            from services.tool_contracts import ToolExecutionContext

            execution_context = ToolExecutionContext(
                user_context=user_context,
                tool_outputs={},
                conversation_state={}
            )

            exec_result = await self.executor.execute(tool, parameters, execution_context)
        except Exception as e:
            logger.error(f"❌ Tool execution exception: {e}", exc_info=True)
            return {
                "success": False,
                "data": {"error": str(e)},
                "error": str(e),
                "ai_feedback": (
                    f"Technical error during {tool_name} execution: {str(e)}. "
                    f"This may be a system issue. Try a different approach or ask user to rephrase."
                ),
                "final_response": (
                    f"⚠️ Izvinjavam se, došlo je do tehničkog problema.\n"
                    f"Možete li pokušati drugačije formulirati zahtjev?"
                )
            }

        # CRITICAL FIX: exec_result is ToolExecutionResult (Pydantic), not dict
        if exec_result.success:
            # Convert to dict for formatter compatibility
            result_dict = {
                "success": True,
                "data": exec_result.data,
                "operation": tool_name
            }
            response = self.formatter.format_result(result_dict, tool)
            return {
                "success": True,
                "data": exec_result.data,
                "final_response": response
            }
        else:
            # Return error with AI feedback for self-correction
            error = exec_result.error_message or "Nepoznata greška"
            ai_feedback = exec_result.ai_feedback or error

            # Provide human-readable error feedback to user
            human_error = self._translate_error_for_user(error, tool_name)

            return {
                "success": False,
                "data": {"error": error},
                "error": error,
                "ai_feedback": ai_feedback,
                "final_response": human_error
            }
    
    async def _handle_availability(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        user_context: Dict[str, Any],
        conv_manager: ConversationManager
    ) -> Dict[str, Any]:
        """Handle vehicle availability check."""
        # CRITICAL FIX: Get tool object and create proper execution context
        tool = self.registry.get_tool(tool_name)
        if not tool:
            return {
                "success": False,
                "error": f"Tool {tool_name} not found",
                "final_response": f"⚠️ Tehnički problem - alat '{tool_name}' nije pronađen."
            }

        from services.tool_contracts import ToolExecutionContext
        execution_context = ToolExecutionContext(
            user_context=user_context,
            tool_outputs={},
            conversation_state={}
        )

        result = await self.executor.execute(tool, parameters, execution_context)

        # CRITICAL FIX: result is ToolExecutionResult (Pydantic), not dict
        if not result.success:
            return {
                "success": False,
                "data": {"error": result.error_message},
                "ai_feedback": result.ai_feedback or "Availability check failed",
                "final_response": f"❌ Greška: {result.error_message}"
            }

        # Extract items from result.data
        items = result.data.get("items", []) if isinstance(result.data, dict) else []
        
        if not items:
            return {
                "success": True,
                "data": result,
                "final_response": (
                    "Nažalost, nema slobodnih vozila za odabrani period.\n"
                    "Možete li odabrati drugi termin?"
                )
            }
        
        # Store for selection
        await conv_manager.set_displayed_items(items)
        await conv_manager.add_parameters(parameters)
        
        response = self.formatter.format_vehicle_list(items)
        
        return {
            "success": True,
            "data": result,
            "needs_input": True,
            "prompt": response
        }
    
    async def _request_confirmation(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        user_context: Dict[str, Any],
        conv_manager: ConversationManager
    ) -> Dict[str, Any]:
        """
        Request confirmation for critical operation.

        Args:
            tool_name: Name of tool to execute
            parameters: Tool parameters
            user_context: User context
            conv_manager: Conversation manager

        Returns:
            Dict with needs_input=True and confirmation prompt
        """
        await conv_manager.add_parameters(parameters)

        tool = self.registry.get_tool(tool_name)
        # CRITICAL FIX: Access Pydantic property directly
        desc = tool.description[:100] if tool and tool.description else tool_name
        
        message = f"📋 **Potvrda operacije:**\n\n{desc}\n\n"
        
        for key, value in list(parameters.items())[:5]:
            if value is not None:
                message += f"• {key}: {value}\n"
        
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
    
    def _requires_confirmation(self, tool_name: str) -> bool:
        """Check if tool requires confirmation."""
        confirm_patterns = ["calendar", "booking", "case", "delete", "create", "update"]
        return any(p in tool_name.lower() for p in confirm_patterns)

    def _translate_error_for_user(self, error: str, tool_name: str) -> str:
        """
        Translate technical error to human-readable Croatian message.

        Args:
            error: Technical error message
            tool_name: Name of tool that failed

        Returns:
            Human-readable error message in Croatian
        """
        error_lower = error.lower()

        # Common error patterns
        if "not found" in error_lower or "404" in error_lower:
            return (
                f"❌ Traženi resurs nije pronađen.\n"
                f"Možete li ponoviti zahtjev sa drugim parametrima?"
            )

        if "permission" in error_lower or "403" in error_lower or "unauthorized" in error_lower:
            return (
                f"❌ Nemate dozvolu za ovu operaciju.\n"
                f"Kontaktirajte administratora ako mislite da je ovo greška."
            )

        if "timeout" in error_lower or "timed out" in error_lower:
            return (
                f"⏱️ Zahtjev je istekao.\n"
                f"Molim pokušajte ponovno za trenutak."
            )

        if "connection" in error_lower or "network" in error_lower:
            return (
                f"🌐 Problem s mrežom.\n"
                f"Molim pokušajte ponovno."
            )

        if "validation" in error_lower or "invalid" in error_lower:
            return (
                f"⚠️ Nevažeći podaci.\n"
                f"Provjerite unesene informacije i pokušajte ponovno."
            )

        # Generic error with tool name context
        return (
            f"❌ Došlo je do greške kod operacije '{tool_name}'.\n"
            f"Detalji: {error}\n\n"
            f"Pokušajte reformulirati zahtjev ili kontaktirajte podršku."
        )
    
    async def _handle_selection(
        self,
        sender: str,
        text: str,
        user_context: Dict[str, Any],
        conv_manager: ConversationManager
    ) -> str:
        """Handle item selection."""
        selected = conv_manager.parse_item_selection(text)
        
        if not selected:
            return "Nisam razumio odabir.\nMolimo navedite broj (npr. '1') ili naziv."
        
        await conv_manager.select_item(selected)
        
        # Build confirmation
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
            f"📋 **Potvrdite rezervaciju:**\n\n"
            f"🚗 Vozilo: {vehicle_name} ({plate})\n"
            f"📅 Od: {from_time}\n"
            f"📅 Do: {to_time}\n\n"
            f"_Potvrdite s 'Da' ili odustanite s 'Ne'._"
        )
        
        await conv_manager.request_confirmation(message)
        conv_manager.context.current_tool = "post_VehicleCalendar"
        await conv_manager.save()
        
        return message
    
    async def _handle_confirmation(
        self,
        sender: str,
        text: str,
        user_context: Dict[str, Any],
        conv_manager: ConversationManager
    ) -> str:
        """Handle confirmation response."""
        confirmation = conv_manager.parse_confirmation(text)
        
        if confirmation is None:
            return "Molim potvrdite s 'Da' ili odustanite s 'Ne'."
        
        if not confirmation:
            await conv_manager.cancel()
            return "✅ Operacija otkazana. Kako vam još mogu pomoći?"
        
        # Execute the confirmed operation
        await conv_manager.confirm()
        
        tool_name = conv_manager.get_current_tool()
        params = conv_manager.get_parameters()
        selected = conv_manager.get_selected_item()
        
        # Add selected item data
        if selected:
            vehicle_id = selected.get("Id") or selected.get("VehicleId")
            if vehicle_id:
                params["VehicleId"] = vehicle_id
        
        # Normalize time params
        if "from" in params and "FromTime" not in params:
            params["FromTime"] = params.pop("from")
        if "to" in params and "ToTime" not in params:
            params["ToTime"] = params.pop("to")

        # CRITICAL FIX: Get tool object and create proper execution context
        tool = self.registry.get_tool(tool_name)
        if not tool:
            await conv_manager.reset()
            return f"⚠️ Tehnički problem - alat '{tool_name}' nije pronađen."

        from services.tool_contracts import ToolExecutionContext
        execution_context = ToolExecutionContext(
            user_context=user_context,
            tool_outputs={},
            conversation_state={}
        )

        # Execute
        result = await self.executor.execute(tool, params, execution_context)
        
        # Complete flow
        await conv_manager.complete()
        await conv_manager.reset()

        # CRITICAL FIX: result is ToolExecutionResult (Pydantic), not dict
        if result.success:
            # Extract created_id from result.data if available
            created_id = ""
            if isinstance(result.data, dict):
                created_id = result.data.get("created_id", "") or result.data.get("Id", "")

            return (
                f"✅ **Operacija uspješna!**\n\n"
                f"{'📝 ID: ' + str(created_id) if created_id else ''}\n\n"
                f"Kako vam još mogu pomoći?"
            )
        else:
            error = result.error_message or "Nepoznata greška"
            return f"❌ Greška: {error}"
    
    async def _handle_gathering(
        self,
        sender: str,
        text: str,
        user_context: Dict[str, Any],
        conv_manager: ConversationManager
    ) -> str:
        """Handle parameter gathering."""
        missing = conv_manager.get_missing_params()
        
        # Try to extract parameters from user input
        extracted = await self.ai.extract_parameters(
            text,
            [{"name": p, "type": "string", "description": ""} for p in missing]
        )
        
        await conv_manager.add_parameters(extracted)
        
        if conv_manager.has_all_required_params():
            # All params collected, continue processing
            return await self._handle_new_request(sender, text, user_context, conv_manager)
        
        still_missing = conv_manager.get_missing_params()
        return self._build_param_prompt(still_missing)
    
    def _build_param_prompt(self, missing: List[str]) -> str:
        """Build prompt for missing params."""
        prompts = {
            "from": "Od kada vam treba? (npr. 'sutra u 9:00')",
            "to": "Do kada? (npr. 'sutra u 17:00')",
            "FromTime": "Od kada vam treba?",
            "ToTime": "Do kada?",
            "Description": "Možete li opisati situaciju?",
            "VehicleId": "Koje vozilo želite?"
        }
        
        if len(missing) == 1:
            return prompts.get(missing[0], f"Trebam još: {missing[0]}")
        
        lines = ["Za nastavak trebam još informacije:"]
        for param in missing[:3]:
            lines.append(f"• {prompts.get(param, param)}")
        
        return "\n".join(lines)
