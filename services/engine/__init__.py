"""
Message Engine - Public API facade.
Version: 16.2 (With Deterministic Routing & All Flow Types)

This module provides backward-compatible interface to the refactored engine.
All existing imports from services.message_engine should work unchanged.

New in v16.0:
- ChainPlanner for multi-step execution plans with fallbacks
- ExecutorWithFallback for automatic retry and alternative tools
- LLMResponseExtractor for intelligent data extraction

New in v16.1:
- QueryRouter for deterministic pattern-based routing
- Direct response for greetings, thanks, help

New in v16.2:
- Domain detection for unknown queries (vehicle, booking, person, support)
- Domain fallback tools when no exact pattern match
- Full flow support: simple, booking, mileage_input, list, case_creation
- Graceful fallback to LLM when deterministic path fails
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

# NEW v16.0: Advanced components for 100% reliability
from services.chain_planner import ChainPlanner, get_chain_planner
from services.executor_fallback import ExecutorWithFallback, get_executor_with_fallback
from services.response_extractor import LLMResponseExtractor, get_response_extractor

# NEW v16.1: Deterministic query routing - NO LLM guessing for known patterns
from services.query_router import QueryRouter, get_query_router, RouteResult

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

        # NEW v16.0: Advanced components
        self.chain_planner = get_chain_planner()
        self.response_extractor = get_response_extractor()
        # Note: executor_fallback initialized lazily when search_engine is available

        # NEW v16.1: Deterministic query router
        self.query_router = get_query_router()

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

        logger.info("MessageEngine initialized (v16.2 with deterministic routing & all flow types)")

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

        # DEBUG: Log current state for troubleshooting flow issues
        logger.info(
            f"STATE CHECK: user={sender[-4:]}, state={state.value}, "
            f"flow={conv_manager.get_current_flow()}, "
            f"tool={conv_manager.get_current_tool()}, "
            f"missing={conv_manager.get_missing_params()}"
        )

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
        """Handle new request with Chain Planning and Fallback Execution."""

        # === v16.1: DETERMINISTIC ROUTING - Try rules FIRST ===
        # This guarantees correct responses for known patterns WITHOUT LLM
        route = self.query_router.route(text, user_context)

        if route.matched:
            logger.info(f"ROUTER: Deterministic match → {route.tool_name or route.flow_type}")

            # Direct response (greetings, thanks, help)
            if route.flow_type == "direct_response":
                return route.response_template

            # Booking flow
            if route.flow_type == "booking":
                return await self._handle_booking_flow(text, user_context, conv_manager)

            # Mileage input flow
            if route.flow_type == "mileage_input":
                return await self._handle_mileage_input_flow(text, user_context, conv_manager)

            # Simple query - execute tool deterministically
            if route.flow_type == "simple" and route.tool_name:
                result = await self._execute_deterministic(
                    route, user_context, conv_manager, sender, text
                )
                if result:
                    return result
                # If deterministic execution failed, fall through to LLM path
                logger.warning("Deterministic execution failed, falling back to LLM")

            # List flow (my bookings, etc.)
            if route.flow_type == "list" and route.tool_name:
                result = await self._execute_deterministic(
                    route, user_context, conv_manager, sender, text
                )
                if result:
                    return result
                logger.warning("List execution failed, falling back to LLM")

            # Case creation flow (report damage, etc.)
            if route.flow_type == "case_creation" and route.tool_name:
                return await self._handle_case_creation_flow(text, user_context, conv_manager)

            # Domain fallback - we detected the domain but no exact pattern
            # Use LLM extraction for the response since we don't have a template
            if route.flow_type == "domain_fallback" and route.tool_name:
                logger.info(f"ROUTER: Domain fallback → {route.tool_name} (confidence={route.confidence})")
                result = await self._execute_deterministic(
                    route, user_context, conv_manager, sender, text
                )
                if result:
                    return result
                # Domain fallback failed - continue to full LLM path
                logger.warning(f"Domain fallback {route.tool_name} failed, using full LLM path")

        # No match at all - check if we should ask for clarification
        elif not route.matched:
            logger.info(f"ROUTER: No match - {route.reason}")
            # Continue to LLM path - it may be able to handle it

        # === END DETERMINISTIC ROUTING ===

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
            text, top_k=10  # v16.0: More tools for better fallback options
        )

        tools_with_scores = sorted(
            tools_with_scores,
            key=lambda t: t["score"],
            reverse=True
        )

        tools = [t["schema"] for t in tools_with_scores]

        # === v16.0: Use ChainPlanner for multi-step plans with fallbacks ===
        plan = await self.chain_planner.create_plan(
            query=text,
            user_context=user_context,
            available_tools=tools,
            tool_scores=tools_with_scores
        )

        logger.info(f"ChainPlan: {plan.understanding} (simple={plan.is_simple})")

        # Handle direct response (greetings, clarifications)
        if plan.direct_response:
            logger.info("Planner direct response")
            return plan.direct_response

        # Handle missing data - start gathering flow
        if plan.missing_data and not plan.has_all_data:
            missing_prompt = self._build_missing_data_prompt(plan.missing_data)
            logger.info(f"Missing data: {plan.missing_data}")

            # v16.0: Use primary_path instead of steps
            first_step = plan.primary_path[0] if plan.primary_path else None

            # Store plan in conversation for continuation
            await conv_manager.start_flow(
                flow_name="gathering",
                tool=first_step.tool_name if first_step else None,
                required_params=plan.missing_data
            )
            await conv_manager.save()

            return missing_prompt

        # v16.0: Store extraction hint for response formatting
        extraction_hint = getattr(plan, 'extraction_hint', None)

        # v16.0: Get fallback tools from plan
        fallback_tools = []
        if hasattr(plan, 'fallback_paths') and plan.fallback_paths:
            for step_fallbacks in plan.fallback_paths.values():
                for fb in step_fallbacks:
                    for fb_step in fb.steps:
                        if fb_step.tool_name:
                            fallback_tools.append(fb_step.tool_name)

        # === Continue with AI tool calling for execution ===
        forced_tool = None

        # CRITICAL FIX v15.1: SINGLE_TOOL_THRESHOLD must match ai_orchestrator
        SINGLE_TOOL_THRESHOLD = 0.98

        if tools_with_scores:
            best_match = max(tools_with_scores, key=lambda t: t["score"])
            best_score = best_match["score"]
            best_tool_name = best_match["name"]

            tool_names = [t["name"] for t in tools_with_scores]
            available_tool_names = set(tool_names)
            logger.info(f"Available tools: {tool_names[:5]}")
            logger.info(f"Best match: {best_tool_name} (score={best_score:.3f})")
            if fallback_tools:
                logger.info(f"Fallback tools: {fallback_tools}")

            # v16.0: Get first step from primary_path
            first_step = plan.primary_path[0] if plan.primary_path else None

            # CRITICAL FIX v15.1: If we're in SINGLE TOOL MODE, only best tool is valid
            # Token budgeting will reduce to only the best tool when score >= 0.98
            # So forced_tool MUST be best_tool_name, otherwise we get OpenAI 400 error
            if best_score >= SINGLE_TOOL_THRESHOLD:
                # SINGLE TOOL MODE - only best match will be sent to OpenAI
                forced_tool = best_tool_name
                if first_step and first_step.tool_name:
                    suggested = first_step.tool_name
                    if suggested != best_tool_name:
                        logger.warning(
                            f"PLANNER suggested '{suggested}' but SINGLE TOOL MODE active "
                            f"(score={best_score:.3f} >= {SINGLE_TOOL_THRESHOLD}). "
                            f"Using best match '{best_tool_name}' instead."
                        )
                    else:
                        logger.info(f"PLANNER: Confirmed best match {forced_tool}")
                else:
                    logger.info(f"SINGLE TOOL MODE: Using {forced_tool}")
            else:
                # Normal mode - can use planner's suggestion if it's in available tools
                if first_step and first_step.tool_name:
                    suggested_tool = first_step.tool_name
                    if suggested_tool in available_tool_names:
                        forced_tool = suggested_tool
                        logger.info(f"PLANNER: Using suggested tool {forced_tool}")
                    else:
                        # Planner suggested a tool not in search results
                        forced_tool = best_tool_name if best_score >= settings.ACTION_THRESHOLD else None
                        logger.warning(
                            f"PLANNER suggested '{suggested_tool}' not in available tools. "
                            f"Using best match '{forced_tool}' instead."
                        )
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

                # v16.0: Use LLM Response Extractor for successful responses
                if tool_response.get("success") and tool_response.get("data"):
                    try:
                        extracted_response = await self.response_extractor.extract(
                            user_query=text,
                            api_response=tool_response["data"],
                            tool_name=tool_name,
                            extraction_hint=extraction_hint
                        )
                        if extracted_response:
                            logger.info(f"LLM extracted response for {tool_name}")
                            return extracted_response
                    except Exception as e:
                        logger.warning(f"LLM extraction failed, using fallback: {e}")
                        # Continue with standard formatting

                if tool_response.get("final_response"):
                    return tool_response["final_response"]

                if tool_response.get("needs_input"):
                    return tool_response.get("prompt", "")

                # v16.0: Try fallback tools on failure
                if not tool_response.get("success", True) and fallback_tools:
                    logger.info(f"Primary tool {tool_name} failed, trying fallbacks...")

                    for fb_tool_name in fallback_tools:
                        if fb_tool_name == tool_name:
                            continue  # Skip the one that just failed

                        fb_tool = self.registry.get_tool(fb_tool_name)
                        if not fb_tool:
                            continue

                        logger.info(f"Trying fallback: {fb_tool_name}")

                        # Adapt parameters for fallback tool
                        fb_params = result["parameters"].copy()

                        fb_result = {
                            "tool": fb_tool_name,
                            "parameters": fb_params,
                            "tool_call_id": result.get("tool_call_id", "fallback")
                        }

                        fb_response = await self._tool_handler.execute_tool_call(
                            fb_result, user_context, conv_manager, sender, user_query=text
                        )

                        if fb_response.get("success"):
                            logger.info(f"Fallback {fb_tool_name} succeeded!")

                            # Use LLM extraction for fallback response too
                            if fb_response.get("data"):
                                try:
                                    extracted = await self.response_extractor.extract(
                                        user_query=text,
                                        api_response=fb_response["data"],
                                        tool_name=fb_tool_name,
                                        extraction_hint=extraction_hint
                                    )
                                    if extracted:
                                        return extracted
                                except Exception as e:
                                    logger.warning(f"Fallback extraction failed: {e}")

                            if fb_response.get("final_response"):
                                return fb_response["final_response"]
                            break  # Success - exit fallback loop

                        logger.info(f"Fallback {fb_tool_name} also failed")

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

    # === v16.1: DETERMINISTIC EXECUTION METHODS ===

    async def _execute_deterministic(
        self,
        route: RouteResult,
        user_context: Dict[str, Any],
        conv_manager: ConversationManager,
        sender: str,
        original_query: str
    ) -> Optional[str]:
        """
        Execute tool deterministically without LLM tool selection.

        This is the FAST PATH for known queries:
        - NO embedding search
        - NO LLM tool selection
        - GUARANTEED correct tool

        Returns:
            Formatted response string or None if should fall back to LLM
        """
        tool = self.registry.get_tool(route.tool_name)
        if not tool:
            logger.error(f"ROUTER: Tool {route.tool_name} not found in registry")
            return None

        # Build parameters from context
        params = {}
        vehicle = user_context.get("vehicle", {})

        # Inject VehicleId if needed and available
        if "VehicleId" in [p.name for p in tool.parameters.values() if p.required]:
            vehicle_id = vehicle.get("id")
            if vehicle_id:
                params["VehicleId"] = vehicle_id
            else:
                # No vehicle - can't execute
                return (
                    "Trenutno nemate dodijeljeno vozilo.\n"
                    "Želite li rezervirati vozilo?"
                )

        # Create execution context
        from services.tool_contracts import ToolExecutionContext
        execution_context = ToolExecutionContext(
            user_context=user_context,
            tool_outputs=conv_manager.context.tool_outputs if hasattr(conv_manager.context, 'tool_outputs') else {},
            conversation_state={}
        )

        # Execute tool
        logger.info(f"DETERMINISTIC: Executing {route.tool_name} with {list(params.keys())}")
        result = await self.executor.execute(tool, params, execution_context)

        if not result.success:
            logger.warning(f"DETERMINISTIC: {route.tool_name} failed: {result.error_message}")
            return None  # Fall back to LLM

        # Try template-based formatting first
        if route.response_template:
            formatted = self.query_router.format_response(route, result.data, original_query)
            if formatted:
                logger.info(f"DETERMINISTIC: Template response for {route.tool_name}")
                return formatted

        # Use LLM extraction for complex responses
        try:
            extraction_hint = ",".join(route.extract_fields) if route.extract_fields else None
            extracted = await self.response_extractor.extract(
                user_query=original_query,
                api_response=result.data,
                tool_name=route.tool_name,
                extraction_hint=extraction_hint
            )
            if extracted:
                logger.info(f"DETERMINISTIC: LLM extracted response for {route.tool_name}")
                return extracted
        except Exception as e:
            logger.warning(f"DETERMINISTIC: LLM extraction failed: {e}")

        # Final fallback - use standard formatter
        result_dict = {"success": True, "data": result.data, "operation": route.tool_name}
        return self.formatter.format_result(result_dict, tool, user_query=original_query)

    async def _handle_booking_flow(
        self,
        text: str,
        user_context: Dict[str, Any],
        conv_manager: ConversationManager
    ) -> str:
        """Handle booking flow deterministically."""
        # Extract time parameters from text
        time_params = await self.ai.extract_parameters(
            text,
            [
                {"name": "from", "type": "string", "description": "Početno vrijeme"},
                {"name": "to", "type": "string", "description": "Završno vrijeme"}
            ]
        )

        params = {}
        if time_params.get("from"):
            params["FromTime"] = time_params["from"]
            params["from"] = time_params["from"]
        if time_params.get("to"):
            params["ToTime"] = time_params["to"]
            params["to"] = time_params["to"]

        # Start availability flow
        result = {
            "tool": "get_AvailableVehicles",
            "parameters": params,
            "tool_call_id": "booking_flow"
        }

        return await self._handle_availability_flow(result, user_context, conv_manager)

    async def _handle_mileage_input_flow(
        self,
        text: str,
        user_context: Dict[str, Any],
        conv_manager: ConversationManager
    ) -> str:
        """Handle mileage input flow deterministically."""
        # Extract mileage value from text
        mileage_params = await self.ai.extract_parameters(
            text,
            [
                {"name": "Value", "type": "number", "description": "Kilometraža u km"}
            ]
        )

        # Try to get vehicle from multiple sources
        vehicle = user_context.get("vehicle", {})
        vehicle_id = vehicle.get("id")
        vehicle_name = vehicle.get("name", "")
        plate = vehicle.get("plate", "")

        # 1. Check if we have vehicle from recent booking/context
        if not vehicle_id and hasattr(conv_manager.context, 'tool_outputs'):
            vehicle_id = conv_manager.context.tool_outputs.get("VehicleId")
            if vehicle_id:
                # Try to get name from stored vehicles
                all_vehicles = conv_manager.context.tool_outputs.get("all_available_vehicles", [])
                for v in all_vehicles:
                    if v.get("Id") == vehicle_id:
                        vehicle_name = v.get("DisplayName") or v.get("FullVehicleName") or "Vozilo"
                        plate = v.get("LicencePlate") or ""
                        break

        # 2. If still no vehicle, fetch first available one
        if not vehicle_id:
            try:
                from services.api_gateway import HttpMethod
                from datetime import datetime, timedelta

                tomorrow = datetime.now() + timedelta(days=1)
                result = await self.gateway.execute(
                    method=HttpMethod.GET,
                    path="/vehiclemgt/AvailableVehicles",
                    params={
                        "from": tomorrow.replace(hour=8, minute=0).isoformat(),
                        "to": tomorrow.replace(hour=17, minute=0).isoformat()
                    }
                )

                if result.success and result.data:
                    data = result.data.get("Data", result.data) if isinstance(result.data, dict) else result.data
                    vehicles = data if isinstance(data, list) else [data]

                    if vehicles:
                        v = vehicles[0]
                        vehicle_id = v.get("Id")
                        vehicle_name = v.get("DisplayName") or v.get("FullVehicleName") or "Vozilo"
                        plate = v.get("LicencePlate") or ""

                        # Store for later
                        if hasattr(conv_manager.context, 'tool_outputs'):
                            conv_manager.context.tool_outputs["VehicleId"] = vehicle_id
                            conv_manager.context.tool_outputs["all_available_vehicles"] = vehicles
            except Exception as e:
                logger.warning(f"Failed to fetch vehicles for mileage: {e}")

        if not vehicle_id:
            return (
                "Nije pronađeno vozilo za unos kilometraže.\n"
                "Pokušajte prvo rezervirati vozilo ili kontaktirajte podršku."
            )

        if not mileage_params.get("Value"):
            # Start gathering flow - store vehicle info
            await conv_manager.start_flow(
                flow_name="mileage_input",
                tool="post_AddMileage",
                required_params=["Value"]
            )
            await conv_manager.add_parameters({
                "VehicleId": vehicle_id,
                "_vehicle_name": vehicle_name,
                "_vehicle_plate": plate
            })
            await conv_manager.save()

            return (
                f"Unosim kilometražu za **{vehicle_name}** ({plate}).\n\n"
                f"Kolika je trenutna kilometraža? _(npr. '14500')_"
            )

        # Have all params - ask for confirmation
        value = mileage_params["Value"]

        await conv_manager.add_parameters({
            "VehicleId": vehicle_id,
            "Value": value
        })

        message = (
            f"**Potvrda unosa kilometraže:**\n\n"
            f"Vozilo: {vehicle_name} ({plate})\n"
            f"Kilometraža: {value} km\n\n"
            f"_Potvrdite s 'Da' ili odustanite s 'Ne'._"
        )

        await conv_manager.request_confirmation(message)
        conv_manager.context.current_tool = "post_AddMileage"
        await conv_manager.save()

        return message

    async def _handle_case_creation_flow(
        self,
        text: str,
        user_context: Dict[str, Any],
        conv_manager: ConversationManager
    ) -> str:
        """Handle support case/damage report creation deterministically."""
        # Extract description from text
        case_params = await self.ai.extract_parameters(
            text,
            [
                {"name": "Description", "type": "string", "description": "Opis problema ili kvara"},
                {"name": "Subject", "type": "string", "description": "Naslov slučaja"}
            ]
        )

        vehicle = user_context.get("vehicle", {})
        vehicle_id = vehicle.get("id")
        vehicle_name = vehicle.get("name", "vozilo")
        plate = vehicle.get("plate", "")

        # Build subject from text if not extracted
        subject = case_params.get("Subject")
        if not subject:
            # Try to infer subject from common patterns
            text_lower = text.lower()
            if "kvar" in text_lower:
                subject = "Prijava kvara"
            elif "šteta" in text_lower or "oštećen" in text_lower:
                subject = "Prijava oštećenja"
            elif "problem" in text_lower:
                subject = "Prijava problema"
            else:
                subject = "Prijava slučaja"

        description = case_params.get("Description")

        if not description:
            # Need to gather description
            await conv_manager.start_flow(
                flow_name="case_creation",
                tool="post_AddCase",
                required_params=["Description"]
            )

            # Store what we have so far
            params = {"Subject": subject}
            if vehicle_id:
                params["VehicleId"] = vehicle_id
            await conv_manager.add_parameters(params)
            await conv_manager.save()

            return "Možete li opisati problem ili kvar detaljnije?"

        # Have all data - request confirmation
        # API expects: User, Subject, Message
        person_id = user_context.get("person_id", "")
        params = {
            "User": person_id,  # Required by API
            "Subject": subject,
            "Message": description  # API uses "Message", not "Description"
        }

        await conv_manager.add_parameters(params)

        vehicle_line = f"Vozilo: {vehicle_name} ({plate})\n" if vehicle_id else ""

        message = (
            f"**Potvrda prijave slučaja:**\n\n"
            f"Naslov: {subject}\n"
            f"{vehicle_line}"
            f"Opis: {description}\n\n"
            f"_Potvrdite s 'Da' ili odustanite s 'Ne'._"
        )

        await conv_manager.request_confirmation(message)
        conv_manager.context.current_tool = "post_AddCase"
        await conv_manager.save()

        return message
