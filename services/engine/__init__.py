"""
Message Engine - Public API facade.
Version: 15.0 (With Reasoning Engine)

This module provides backward-compatible interface to the refactored engine.
All existing imports from services.message_engine should work unchanged.
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
from services.dependency_resolver import DependencyResolver
from services.error_learning import ErrorLearningService
from services.reasoning import Planner, ExecutionPlan, PlanStep

from .tool_handler import ToolHandler
from .flow_handler import FlowHandler

logger = logging.getLogger(__name__)
settings = get_settings()

__all__ = ['MessageEngine']


class MessageEngine:
    """
    Main message processing engine.

    Coordinates:
    - User identification
    - Conversation state (Redis-backed)
    - AI interactions with error feedback
    - Tool execution with validation

    This is a facade that coordinates the refactored components.
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
        self.executor = ToolExecutor(gateway)
        self.context = context_service
        self.queue = queue_service
        self.cache = cache_service
        self.db = db_session
        self.redis = context_service.redis if context_service else None

        # Core services
        self.dependency_resolver = DependencyResolver(registry)
        self.error_learning = ErrorLearningService(redis_client=self.redis)
        self.ai = AIOrchestrator()
        self.formatter = ResponseFormatter()
        self.planner = Planner()

        # Refactored handlers
        self._tool_handler = ToolHandler(
            registry=registry,
            executor=self.executor,
            dependency_resolver=self.dependency_resolver,
            error_learning=self.error_learning,
            formatter=self.formatter
        )
        self._flow_handler = FlowHandler(
            registry=registry,
            executor=self.executor,
            ai=self.ai,
            formatter=self.formatter
        )

        logger.info("MessageEngine initialized (v15.0 with reasoning)")

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
        logger.info(f"Processing: {sender[-4:]} - {text[:50]}")

        try:
            # 1. Identify user
            user_context = await self._identify_user(sender)

            if not user_context:
                return (
                    "Vas broj nije pronaden u sustavu MobilityOne.\n"
                    "Molimo kontaktirajte administratora."
                )

            # 2. Load conversation state
            conv_manager = await ConversationManager.load_for_user(sender, self.redis)

            # 3. Check timeout
            if conv_manager.is_timed_out():
                await conv_manager.reset()

            # 4. Add to history
            await self.context.add_message(sender, "user", text)

            # 5. Handle new user greeting
            if user_context.get("is_new"):
                greeting = self._build_greeting(user_context)
                await self.context.add_message(sender, "assistant", greeting)

                response = await self._process_with_state(
                    sender, text, user_context, conv_manager
                )

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
            logger.error(f"Processing error: {e}", exc_info=True)
            return "Doslo je do greske. Molimo pokusajte ponovno."

    async def _identify_user(self, phone: str) -> Optional[Dict[str, Any]]:
        """Identify user and build context."""
        user_service = UserService(self.db, self.gateway, self.cache)

        user = await user_service.get_active_identity(phone)

        if user:
            ctx = await user_service.build_context(user.api_identity, phone)
            ctx["display_name"] = user.display_name
            ctx["is_new"] = False
            return ctx

        result = await user_service.try_auto_onboard(phone)

        if result:
            display_name, vehicle_info = result
            user = await user_service.get_active_identity(phone)

            if user:
                ctx = await user_service.build_context(user.api_identity, phone)
                ctx["display_name"] = display_name
                ctx["is_new"] = True
                ctx["vehicle_info"] = vehicle_info
                return ctx

        return None

    def _build_greeting(self, user_context: Dict[str, Any]) -> str:
        """Build personalized greeting for new user."""
        name = user_context.get("display_name", "")
        vehicle = user_context.get("vehicle", {})
        vehicle_info = user_context.get("vehicle_info", "")

        greeting = f"Pozdrav {name}!\n\n"
        greeting += "Ja sam MobilityOne AI asistent.\n\n"

        if vehicle.get("plate"):
            plate = vehicle.get("plate")
            v_name = vehicle.get("name", "vozilo")
            mileage = vehicle.get("mileage", "N/A")

            greeting += f"Vidim da vam je dodijeljeno vozilo:\n"
            greeting += f"   **{v_name}** ({plate})\n"
            greeting += f"   Kilometraza: {mileage} km\n\n"
            greeting += "Kako vam mogu pomoci?\n"
            greeting += "* Unos kilometraze\n"
            greeting += "* Prijava kvara\n"
            greeting += "* Rezervacija vozila\n"
            greeting += "* Pitanja o vozilu"
        elif vehicle_info and "nema" not in vehicle_info.lower():
            greeting += f"{vehicle_info}\n\n"
            greeting += "Kako vam mogu pomoci?"
        else:
            greeting += "Trenutno nemate dodijeljeno vozilo.\n\n"
            greeting += "Zelite li rezervirati vozilo? Recite mi:\n"
            greeting += "* Za koji period (npr. 'sutra od 8 do 17')\n"
            greeting += "* Ili samo recite 'Trebam vozilo' pa cemo dalje"

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
            return await self._flow_handler.handle_selection(
                sender, text, user_context, conv_manager, self._handle_new_request
            )

        if state == ConversationState.CONFIRMING:
            return await self._flow_handler.handle_confirmation(
                sender, text, user_context, conv_manager
            )

        if state == ConversationState.GATHERING_PARAMS:
            return await self._flow_handler.handle_gathering(
                sender, text, user_context, conv_manager, self._handle_new_request
            )

        # IDLE - new request
        return await self._handle_new_request(sender, text, user_context, conv_manager)

    async def _handle_new_request(
        self,
        sender: str,
        text: str,
        user_context: Dict[str, Any],
        conv_manager: ConversationManager
    ) -> str:
        """Handle new request with Chain of Thought planning."""
        # Pre-resolve entity references
        pre_resolved = await self._pre_resolve_entity_references(
            text, user_context, conv_manager
        )

        if pre_resolved:
            logger.info(f"Pre-resolved entities: {list(pre_resolved.keys())}")

        # Get history
        history = await self.context.get_recent_messages(sender)

        messages = history.copy()
        messages.append({"role": "user", "content": text})

        # Get relevant tools with scores
        tools_with_scores = await self.registry.find_relevant_tools_with_scores(
            text, top_k=5
        )

        tools_with_scores = sorted(
            tools_with_scores,
            key=lambda t: t["score"],
            reverse=True
        )

        tools = [t["schema"] for t in tools_with_scores]

        # === CHAIN OF THOUGHT: Create execution plan ===
        plan = await self.planner.create_plan(
            query=text,
            user_context=user_context,
            available_tools=tools,
            tool_scores=tools_with_scores
        )

        logger.info(f"Plan: {plan.understanding} (simple={plan.is_simple})")

        # Handle direct response (greetings, clarifications)
        if plan.direct_response:
            logger.info("Planner direct response")
            return plan.direct_response

        # Handle missing data - start gathering flow
        if plan.missing_data and not plan.has_all_data:
            missing_prompt = self._build_missing_data_prompt(plan.missing_data)
            logger.info(f"Missing data: {plan.missing_data}")

            # Store plan in conversation for continuation
            await conv_manager.start_flow(
                flow_name="gathering",
                tool=plan.steps[0].tool_name if plan.steps else None,
                required_params=plan.missing_data
            )
            await conv_manager.save()

            return missing_prompt

        # === Continue with AI tool calling for execution ===
        forced_tool = None

        if tools_with_scores:
            best_match = max(tools_with_scores, key=lambda t: t["score"])
            best_score = best_match["score"]
            best_tool_name = best_match["name"]

            tool_names = [t["name"] for t in tools_with_scores]
            logger.info(f"Available tools: {tool_names}")
            logger.info(f"Best match: {best_tool_name} (score={best_score:.3f})")

            # Use planner's suggested tool if available
            if plan.steps and plan.steps[0].tool_name:
                forced_tool = plan.steps[0].tool_name
                logger.info(f"PLANNER: Suggesting {forced_tool}")
            elif best_score >= settings.ACTION_THRESHOLD:
                forced_tool = best_tool_name
                logger.info(f"ACTION-FIRST: Forcing {forced_tool}")

        # Build system prompt
        system_prompt = self.ai.build_system_prompt(
            user_context,
            conv_manager.to_dict() if conv_manager.is_in_flow() else None
        )

        # AI iteration loop
        current_messages = messages.copy()

        for iteration in range(self.MAX_ITERATIONS):
            logger.debug(f"AI iteration {iteration + 1}/{self.MAX_ITERATIONS}")

            current_forced = forced_tool if iteration == 0 else None

            result = await self.ai.analyze(
                messages=current_messages,
                tools=tools,
                system_prompt=system_prompt,
                forced_tool=current_forced,
                tool_scores=tools_with_scores
            )

            if result.get("type") == "error":
                return result.get("content", "Greska u AI obradi.")

            if result.get("type") == "text":
                return result.get("content", "")

            if result.get("type") == "tool_call":
                tool_name = result["tool"]
                tool = self.registry.get_tool(tool_name)
                method = tool.method if tool else "GET"

                # Check for special handling
                if "available" in tool_name.lower() or "calendar" in tool_name.lower():
                    if method == "GET" and "vehicle" in tool_name.lower():
                        return await self._handle_availability_flow(
                            result, user_context, conv_manager
                        )

                if method in ("POST", "PUT", "PATCH"):
                    if self._tool_handler.requires_confirmation(tool_name):
                        flow_result = await self._flow_handler.request_confirmation(
                            tool_name, result["parameters"], user_context, conv_manager
                        )
                        if flow_result.get("needs_input"):
                            return flow_result["prompt"]

                # Execute tool
                # NEW v10.1: Pass original text for intent-aware response formatting
                tool_response = await self._tool_handler.execute_tool_call(
                    result, user_context, conv_manager, sender, user_query=text
                )

                if tool_response.get("final_response"):
                    return tool_response["final_response"]

                if tool_response.get("needs_input"):
                    return tool_response.get("prompt", "")

                # Add to conversation for next iteration
                tool_result_content = tool_response.get("data", {})

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

        return "Nisam uspio obraditi zahtjev. Pokusajte drugacije formulirati."

    async def _handle_availability_flow(
        self,
        result: Dict[str, Any],
        user_context: Dict[str, Any],
        conv_manager: ConversationManager
    ) -> str:
        """Handle availability check flow."""
        flow_result = await self._flow_handler.handle_availability(
            result["tool"],
            result["parameters"],
            user_context,
            conv_manager
        )

        if flow_result.get("needs_input"):
            return flow_result["prompt"]
        if flow_result.get("final_response"):
            return flow_result["final_response"]

        return flow_result.get("error", "Greska pri provjeri dostupnosti.")

    async def _pre_resolve_entity_references(
        self,
        text: str,
        user_context: Dict[str, Any],
        conv_manager: ConversationManager
    ) -> Dict[str, Any]:
        """Pre-resolve entity references before AI processing."""
        resolved = {}

        try:
            entity_ref = self.dependency_resolver.detect_entity_reference(
                text, entity_type="vehicle"
            )

            if entity_ref:
                logger.info(f"Pre-resolving entity: {entity_ref}")

                resolution = await self.dependency_resolver.resolve_entity_reference(
                    reference=entity_ref,
                    user_context=user_context,
                    executor=self.executor
                )

                if resolution.success:
                    logger.info(f"Pre-resolved VehicleId: {resolution.resolved_value}")

                    resolved["VehicleId"] = resolution.resolved_value
                    resolved["vehicleId"] = resolution.resolved_value

                    if hasattr(conv_manager.context, 'tool_outputs'):
                        conv_manager.context.tool_outputs["VehicleId"] = (
                            resolution.resolved_value
                        )
                        conv_manager.context.tool_outputs["vehicleId"] = (
                            resolution.resolved_value
                        )
                        await conv_manager.save()
                else:
                    logger.warning(
                        f"Failed to pre-resolve: {resolution.error_message}"
                    )

        except Exception as e:
            logger.error(f"Entity pre-resolution error: {e}", exc_info=True)

        return resolved

    def _build_missing_data_prompt(self, missing_params: List[str]) -> str:
        """Build user-friendly prompt for missing parameters."""
        param_prompts = {
            "from": "Od kada vam treba? (npr. 'sutra u 9:00')",
            "to": "Do kada?",
            "FromTime": "Od kada vam treba?",
            "ToTime": "Do kada?",
            "Description": "Mozete li opisati situaciju?",
            "VehicleId": "Koje vozilo zelite?",
            "Value": "Koja je vrijednost? (npr. kilometraza)",
            "Subject": "Koji je naslov/tema?",
            "Message": "Koja je poruka?"
        }

        if len(missing_params) == 1:
            param = missing_params[0]
            return param_prompts.get(param, f"Trebam jos: {param}")

        lines = ["Za nastavak trebam jos informacije:"]
        for param in missing_params[:3]:
            prompt = param_prompts.get(param, param)
            lines.append(f"* {prompt}")

        return "\n".join(lines)
