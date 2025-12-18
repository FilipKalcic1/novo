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
        self.executor = ToolExecutor(gateway, registry)
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
        """Handle new request with AI tool calling."""
        # Get history
        history = await self.context.get_recent_messages(sender)
        
        # Build messages for AI
        messages = history.copy()
        messages.append({"role": "user", "content": text})
        
        # Get relevant tools
        tools = await self.registry.find_relevant_tools(text, top_k=5)
        
        if tools:
            tool_names = [t["function"]["name"] for t in tools]
            logger.info(f"🔧 Available tools: {tool_names}")
        
        # Build system prompt
        system_prompt = self.ai.build_system_prompt(
            user_context,
            conv_manager.to_dict() if conv_manager.is_in_flow() else None
        )
        
        # AI iteration loop with error feedback
        current_messages = messages.copy()
        
        for iteration in range(self.MAX_ITERATIONS):
            logger.debug(f"AI iteration {iteration + 1}/{self.MAX_ITERATIONS}")
            
            result = await self.ai.analyze(
                messages=current_messages,
                tools=tools,
                system_prompt=system_prompt
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
        """Execute tool call with proper error handling."""
        tool_name = result["tool"]
        parameters = result["parameters"]
        
        logger.info(f"🔧 Executing: {tool_name}")
        
        tool = self.registry.get_tool(tool_name)
        
        if not tool:
            return {
                "success": False,
                "data": {"error": f"Tool {tool_name} not found"},
                "ai_feedback": f"Tool '{tool_name}' does not exist. Use a different tool.",
                "final_response": f"Alat '{tool_name}' nije pronađen."
            }
        
        method = tool.get("method", "GET")
        
        # Availability check (returns list for selection)
        if "available" in tool_name.lower() or "calendar" in tool_name.lower():
            if method == "GET" and "vehicle" in tool_name.lower():
                return await self._handle_availability(tool_name, parameters, user_context, conv_manager)
        
        # POST/PUT may need confirmation for critical operations
        if method in ("POST", "PUT", "PATCH"):
            if self._requires_confirmation(tool_name):
                return await self._request_confirmation(tool_name, parameters, user_context, conv_manager)
        
        # Direct execution
        exec_result = await self.executor.execute(tool_name, parameters, user_context)
        
        if exec_result.get("success"):
            response = self.formatter.format_result(exec_result, tool)
            return {
                "success": True,
                "data": exec_result,
                "final_response": response
            }
        else:
            # Return error with AI feedback for self-correction
            error = exec_result.get("error", "Nepoznata greška")
            ai_feedback = exec_result.get("ai_feedback", error)
            
            return {
                "success": False,
                "data": exec_result,
                "error": error,
                "ai_feedback": ai_feedback
                # No final_response - let AI try to recover
            }
    
    async def _handle_availability(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        user_context: Dict[str, Any],
        conv_manager: ConversationManager
    ) -> Dict[str, Any]:
        """Handle vehicle availability check."""
        result = await self.executor.execute(tool_name, parameters, user_context)
        
        if not result.get("success"):
            return {
                "success": False,
                "data": result,
                "ai_feedback": result.get("ai_feedback", "Availability check failed"),
                "final_response": f"❌ Greška: {result.get('error')}"
            }
        
        items = result.get("items", [])
        
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
        """Request confirmation for operation."""
        await conv_manager.add_parameters(parameters)
        
        tool = self.registry.get_tool(tool_name)
        desc = tool.get("description", tool_name)[:100] if tool else tool_name
        
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
        
        # Execute
        result = await self.executor.execute(tool_name, params, user_context)
        
        # Complete flow
        await conv_manager.complete()
        await conv_manager.reset()
        
        if result.get("success"):
            created_id = result.get("created_id", "")
            return (
                f"✅ **Operacija uspješna!**\n\n"
                f"{'📝 ID: ' + str(created_id) if created_id else ''}\n\n"
                f"Kako vam još mogu pomoći?"
            )
        else:
            error = result.get("error", "Nepoznata greška")
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
