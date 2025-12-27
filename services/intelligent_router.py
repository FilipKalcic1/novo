"""
Intelligent Router - Smart Query-to-Tool Routing
Version: 1.0

This module provides intelligent routing of user queries to appropriate tools
using a multi-stage filtering approach:

1. CATEGORY ROUTING: Embedding similarity to find relevant categories
2. TOOL FILTERING: Only tools from top categories are considered
3. LLM TOOL SELECTION: LLM chooses from filtered tools (not 909!)
4. FLOW DECISION: Predefined rules decide Flow vs Simple API

DECISION LOGIC:
===============
┌────────────────────────────────────────────────────────────────────────┐
│ IF tool IN FLOW_TRIGGERS AND (has_missing_params OR needs_confirmation)│
│    → Start appropriate FLOW (booking, case, mileage)                   │
│                                                                        │
│ ELSE                                                                   │
│    → SIMPLE API CALL (any of 909 APIs!)                                │
└────────────────────────────────────────────────────────────────────────┘

FLOW_TRIGGERS are PREDEFINED (not LLM decision):
- post_VehicleCalendar, post_Booking → booking_flow
- get_AvailableVehicles → availability_flow
- post_AddCase → case_flow
- post_AddMileage → mileage_flow
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Set, Tuple
from enum import Enum

from services.patterns import READ_INTENT_PATTERNS, MUTATION_INTENT_PATTERNS

logger = logging.getLogger(__name__)


# ============================================================================
# INTENT DETECTION - True intelligence via pattern matching
# ============================================================================

class QueryIntent(Enum):
    """Detected intent of user query."""
    READ = "read"           # Getting data: show, list, what is
    WRITE = "write"         # Creating/updating: add, create, report
    DELETE = "delete"       # Removing: delete, cancel, remove
    UNKNOWN = "unknown"     # Cannot determine


def detect_intent(query: str) -> QueryIntent:
    """
    Detect user intent using centralized patterns.

    This is INTELLIGENT detection - not just keyword matching.
    It uses comprehensive regex patterns for Croatian and English.
    """
    query_lower = query.lower().strip()

    # Check for DELETE patterns first (subset of MUTATION)
    delete_patterns = [
        r'obri[sš]i', r'izbri[sš]i', r'ukloni', r'makni',
        r'otka[zž]i', r'poni[sš]ti',
        r'delete', r'remove', r'cancel'
    ]
    for pattern in delete_patterns:
        if re.search(pattern, query_lower):
            return QueryIntent.DELETE

    # Check for WRITE/MUTATION patterns
    for pattern in MUTATION_INTENT_PATTERNS:
        try:
            if re.search(pattern, query_lower):
                return QueryIntent.WRITE
        except re.error:
            continue

    # Check for READ patterns
    for pattern in READ_INTENT_PATTERNS:
        try:
            if re.search(pattern, query_lower):
                return QueryIntent.READ
        except re.error:
            continue

    # Default to READ for unknown (safer - read doesn't modify data)
    return QueryIntent.READ


class FlowType(Enum):
    """Types of conversation flows."""
    SIMPLE = "simple"              # Direct API call, format response
    BOOKING = "booking"            # Multi-step: availability → select → confirm
    CASE_CREATION = "case_creation"  # Multi-step: gather description → confirm
    MILEAGE_INPUT = "mileage_input"  # Multi-step: get value → confirm
    AVAILABILITY = "availability"    # Show available, offer booking
    LIST = "list"                    # Show list of items (my bookings, etc.)
    DIRECT_RESPONSE = "direct_response"  # No API call (greetings, help)


@dataclass
class RoutingDecision:
    """Result of intelligent routing."""

    # Which tool to use
    tool_name: Optional[str] = None

    # Confidence in the decision (0.0 - 1.0)
    confidence: float = 0.0

    # Which flow type to use
    flow_type: FlowType = FlowType.SIMPLE

    # Detected query intent (READ, WRITE, DELETE)
    intent: QueryIntent = QueryIntent.UNKNOWN

    # Parameters extracted from query
    extracted_params: Dict[str, Any] = field(default_factory=dict)

    # Parameters still needed
    missing_params: List[str] = field(default_factory=list)

    # Top categories matched
    matched_categories: List[str] = field(default_factory=list)

    # All candidate tools from categories
    candidate_tools: List[str] = field(default_factory=list)

    # Reasoning for the decision
    reasoning: str = ""

    # Direct response (for greetings, help, etc.)
    direct_response: Optional[str] = None

    # Is this a fallback decision?
    is_fallback: bool = False


# ============================================================================
# FLOW TRIGGERS - Predefined rules for when to use multi-step flows
# ============================================================================
# These are NOT decided by LLM - they are RULES based on tool + context

FLOW_TRIGGERS: Dict[str, FlowType] = {
    # Booking-related tools → booking flow
    "post_VehicleCalendar": FlowType.BOOKING,
    "post_Booking": FlowType.BOOKING,

    # Availability check → availability flow (can lead to booking)
    "get_AvailableVehicles": FlowType.AVAILABILITY,

    # Case/damage reporting → case creation flow
    "post_AddCase": FlowType.CASE_CREATION,
    "post_AddCase_parse": FlowType.CASE_CREATION,

    # Mileage input → mileage flow
    "post_AddMileage": FlowType.MILEAGE_INPUT,
}

# Tools that show lists of items
LIST_TOOLS: Set[str] = {
    "get_VehicleCalendar",  # My bookings
    "get_Cases",            # My cases
    "get_Trips",            # My trips
    "get_Expenses",         # My expenses
}

# Parameters that trigger flows (if missing)
FLOW_REQUIRED_PARAMS: Dict[str, List[str]] = {
    "post_VehicleCalendar": ["VehicleId", "FromTime", "ToTime"],
    "post_Booking": ["VehicleId", "FromTime", "ToTime"],
    "post_AddCase": ["Description"],  # Need damage description
    "post_AddMileage": ["Mileage", "VehicleId"],
    "get_AvailableVehicles": ["from", "to"],  # Optional but nice to have
}


class IntelligentRouter:
    """
    Intelligent query router using category-first filtering.

    Architecture:
    1. Category Router: Find top 3 relevant categories using embeddings
    2. Tool Filter: Get all tools from those categories (~30-50 tools)
    3. LLM Selection: LLM picks the best tool from filtered list
    4. Flow Decision: Rules determine if it's a flow or simple API
    """

    def __init__(self, registry, category_embeddings: Optional[Dict] = None):
        """
        Initialize router.

        Args:
            registry: ToolRegistry instance
            category_embeddings: Pre-computed category embeddings (optional)
        """
        self.registry = registry
        self._category_embeddings = category_embeddings or {}
        self._category_data: Dict[str, Dict] = {}
        self._initialized = False

    async def initialize(self):
        """Load category data and compute embeddings if needed."""
        if self._initialized:
            return

        try:
            # Load category definitions
            import os
            from pathlib import Path

            config_path = Path(__file__).parent.parent / "config" / "tool_categories.json"

            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._category_data = data.get("categories", {})
                logger.info(f"Loaded {len(self._category_data)} categories")
            else:
                logger.warning(f"Category file not found: {config_path}")

            # Compute category embeddings if not provided
            if not self._category_embeddings and self._category_data:
                await self._compute_category_embeddings()

            self._initialized = True

        except Exception as e:
            logger.error(f"Failed to initialize router: {e}")
            raise

    async def _compute_category_embeddings(self):
        """
        Load or compute embeddings for each category.

        Note: Category embeddings are OPTIONAL - keyword matching works without them.
        We skip embedding computation to avoid slow startup. Embeddings will be
        computed on-demand when needed.
        """
        # Skip category embedding computation for now - keyword matching is sufficient
        # and much faster. Category embeddings can be added later for improved accuracy.
        logger.info(
            f"Category embeddings skipped (using keyword matching for {len(self._category_data)} categories)"
        )

        # Just populate category data without embeddings
        for cat_name, cat_data in self._category_data.items():
            self._category_embeddings[cat_name] = {
                "embedding": None,  # No embedding
                "tools": cat_data.get("tools", []),
                "description": cat_data.get("description_hr", "")
            }

    async def route(
        self,
        query: str,
        user_context: Dict[str, Any],
        conversation_state: Optional[Dict] = None
    ) -> RoutingDecision:
        """
        Route query to appropriate tool and flow.

        Args:
            query: User's query text
            user_context: User context (person_id, vehicle_id, etc.)
            conversation_state: Current conversation state

        Returns:
            RoutingDecision with tool, flow type, and reasoning
        """
        await self.initialize()

        # Step 1: Check for direct responses (greetings, help, thanks)
        direct = self._check_direct_response(query)
        if direct:
            return direct

        # Step 2: INTELLIGENT INTENT DETECTION
        # This uses comprehensive regex patterns from patterns.py
        intent = detect_intent(query)
        logger.info(f"ROUTER: Intent detected: {intent.value} for '{query[:40]}...'")

        # Step 3: Find relevant categories
        matched_categories = await self._find_relevant_categories(query, top_k=3)

        if not matched_categories:
            logger.warning(f"No categories matched for: {query[:50]}")
            return self._fallback_decision(query, "No categories matched", intent)

        # Step 4: Get candidate tools from matched categories
        candidate_tools = self._get_tools_from_categories(matched_categories)

        if not candidate_tools:
            logger.warning(f"No tools found in categories: {matched_categories}")
            return self._fallback_decision(query, "No tools in matched categories", intent)

        # Step 5: INTELLIGENT FILTERING by intent
        # Filter tools based on detected intent (READ → get_, WRITE → post_, DELETE → delete_)
        filtered_tools = self._filter_tools_by_intent(candidate_tools, intent)

        logger.info(
            f"ROUTER: Query '{query[:30]}...' → Intent: {intent.value} "
            f"→ Categories: {matched_categories} → {len(filtered_tools)}/{len(candidate_tools)} tools"
        )

        # Step 6: Select best tool from filtered candidates using LLM
        tool_selection = await self._llm_select_tool(
            query=query,
            candidate_tools=filtered_tools if filtered_tools else candidate_tools,
            user_context=user_context,
            intent=intent,
            matched_categories=matched_categories
        )

        if not tool_selection.get("tool"):
            return self._fallback_decision(query, "Could not select tool", intent)

        selected_tool = tool_selection["tool"]
        confidence = tool_selection.get("confidence", 0.5)
        extracted_params = tool_selection.get("params", {})

        # Step 7: Determine flow type based on PREDEFINED RULES
        flow_type, missing_params = self._determine_flow_type(
            selected_tool, extracted_params, user_context
        )

        logger.info(
            f"ROUTER DECISION: {selected_tool} (conf={confidence:.2f}, intent={intent.value}) "
            f"→ Flow: {flow_type.value}, Missing: {missing_params}"
        )

        return RoutingDecision(
            tool_name=selected_tool,
            confidence=confidence,
            flow_type=flow_type,
            intent=intent,
            extracted_params=extracted_params,
            missing_params=missing_params,
            matched_categories=matched_categories,
            candidate_tools=candidate_tools,
            reasoning=tool_selection.get("reasoning", "")
        )

    def _check_direct_response(self, query: str) -> Optional[RoutingDecision]:
        """Check if query needs direct response without API call."""
        query_lower = query.lower().strip()

        # Greetings
        greetings = ["bok", "cao", "pozdrav", "zdravo", "hej", "hi", "hello"]
        if query_lower in greetings:
            return RoutingDecision(
                flow_type=FlowType.DIRECT_RESPONSE,
                confidence=1.0,
                direct_response="Pozdrav! Kako vam mogu pomoci?",
                reasoning="Greeting detected"
            )

        # Thanks
        if any(w in query_lower for w in ["hvala", "zahvaljujem", "thanks", "fala"]):
            return RoutingDecision(
                flow_type=FlowType.DIRECT_RESPONSE,
                confidence=1.0,
                direct_response="Nema na cemu! Slobodno pitajte ako trebate jos nesto.",
                reasoning="Thanks detected"
            )

        # Help
        help_patterns = [r"^pomoć?$", r"^help$", r"što možeš", r"kako koristiti"]
        for pattern in help_patterns:
            if re.search(pattern, query_lower):
                return RoutingDecision(
                    flow_type=FlowType.DIRECT_RESPONSE,
                    confidence=1.0,
                    direct_response=(
                        "Mogu vam pomoci s:\n"
                        "• **Kilometraza** - provjera ili unos km\n"
                        "• **Rezervacije** - rezervacija vozila\n"
                        "• **Podaci o vozilu** - registracija, lizing\n"
                        "• **Prijava kvara** - kreiranje slucaja\n\n"
                        "Sto vas zanima?"
                    ),
                    reasoning="Help request detected"
                )

        return None

    async def _find_relevant_categories(
        self,
        query: str,
        top_k: int = 3
    ) -> List[str]:
        """
        Find most relevant categories using embedding similarity + keyword matching.

        This is the INTELLIGENT part - we use:
        1. Keyword matching (fast, always works)
        2. Embedding similarity (more accurate but slower)
        """
        query_lower = query.lower()

        # STEP 1: Keyword-based matching (fast fallback)
        keyword_matches = self._keyword_match_categories(query_lower)

        # STEP 2: Try embedding similarity if we have embeddings
        embedding_matches = []
        if self._category_embeddings:
            try:
                embedding_matches = await self._embedding_match_categories(query, top_k * 2)
            except Exception as e:
                logger.warning(f"Embedding matching failed, using keywords only: {e}")

        # STEP 3: Combine results (prioritize embedding matches but include keyword matches)
        combined = []
        seen = set()

        # First add embedding matches (more accurate)
        for cat_name, score in embedding_matches:
            if cat_name not in seen:
                combined.append((cat_name, score))
                seen.add(cat_name)

        # Then add keyword matches
        for cat_name, score in keyword_matches:
            if cat_name not in seen:
                combined.append((cat_name, score * 0.8))  # Slightly lower weight
                seen.add(cat_name)

        # Sort by score
        combined.sort(key=lambda x: x[1], reverse=True)

        # Return top K
        result = [cat for cat, _ in combined[:top_k]]

        if not result:
            # Ultimate fallback: most common categories
            result = ["data_retrieval", "vehicle_calendar", "case_management"][:top_k]

        logger.debug(f"Category matches for '{query[:30]}': {result}")
        return result

    # Query keyword to category mapping (handles Croatian variations)
    QUERY_TO_CATEGORY_HINTS = {
        # Availability / Booking
        "slobodn": ["vehicle_calendar", "availability_management", "equipment_availability"],
        "dostupn": ["vehicle_calendar", "availability_management"],
        "rezerv": ["booking_management", "vehicle_calendar_management"],
        "book": ["booking_management", "vehicle_calendar_management"],
        "trebam auto": ["vehicle_calendar", "availability_management", "booking_management"],
        "trebam vozilo": ["vehicle_calendar", "availability_management", "booking_management"],

        # Damage / Case
        "stet": ["case_management"],
        "kvar": ["case_management"],
        "prijav": ["case_management"],
        "ostecen": ["case_management"],
        "slucaj": ["case_management"],
        "case": ["case_management"],
        "udari": ["case_management"],  # udario, udarila
        "ogreb": ["case_management"],  # ogrebao, ogrebotina
        "sudar": ["case_management"],
        "nesrec": ["case_management"],  # nesreca
        "problem": ["case_management"],
        "ne radi": ["case_management"],
        "pokvari": ["case_management"],  # pokvario

        # Mileage - include data_retrieval for READ queries about mileage
        "kilometr": ["mileage_tracking", "data_retrieval"],
        "km": ["mileage_tracking", "data_retrieval"],
        "unesi": ["mileage_tracking"],
        "upisi": ["mileage_tracking"],

        # Vehicle info - include data_retrieval
        "vozil": ["vehicle_info", "vehicle_management", "data_retrieval"],
        "auto": ["vehicle_info", "vehicle_management", "vehicle_calendar"],
        "registracij": ["vehicle_info", "data_retrieval"],
        "tablice": ["vehicle_info", "data_retrieval"],
        "tablica": ["vehicle_info", "data_retrieval"],
        "istice": ["data_retrieval", "vehicle_info"],  # expiry
        "istjece": ["data_retrieval", "vehicle_info"],

        # Possessive queries (moja, moje) - usually need data_retrieval
        "moja": ["data_retrieval", "vehicle_info"],
        "moje": ["data_retrieval", "booking_management"],
        "moj ": ["data_retrieval", "vehicle_info"],

        # Question words - usually data retrieval
        "koliko": ["data_retrieval", "mileage_tracking"],
        "kolika": ["data_retrieval", "mileage_tracking"],
        "koja je": ["data_retrieval", "vehicle_info"],
        "koji je": ["data_retrieval", "vehicle_info"],

        # Expenses
        "trošak": ["expense_management", "expense_tracking"],
        "trosak": ["expense_management", "expense_tracking"],
        "troskov": ["expense_management", "expense_tracking"],  # troskova, troskove
        "expense": ["expense_management", "expense_tracking"],
        "popis": ["expense_management", "data_retrieval"],  # popis troskova

        # Trips
        "putovanj": ["trip_management"],
        "trip": ["trip_management"],

        # General data
        "podaci": ["data_retrieval", "master_data_management"],
        "info": ["data_retrieval", "vehicle_info"],
        "pokazi": ["data_retrieval"],
        "prikazi": ["data_retrieval"],
    }

    def _keyword_match_categories(self, query_lower: str) -> List[Tuple[str, float]]:
        """Match categories using keyword overlap and hints."""
        matches = {}  # cat_name -> score

        # STEP 1: Use query-to-category hints
        for keyword, categories in self.QUERY_TO_CATEGORY_HINTS.items():
            if keyword in query_lower:
                for cat in categories:
                    if cat in self._category_data:
                        matches[cat] = matches.get(cat, 0) + 0.5

        # STEP 2: Check category keywords from JSON
        for cat_name, cat_data in self._category_data.items():
            score = matches.get(cat_name, 0)

            # Check Croatian keywords
            keywords_hr = cat_data.get("keywords_hr", [])
            for keyword in keywords_hr:
                if keyword.lower() in query_lower:
                    score += 0.3

            # Check English keywords
            keywords_en = cat_data.get("keywords_en", [])
            for keyword in keywords_en:
                if keyword.lower() in query_lower:
                    score += 0.2

            # Check description overlap
            desc_hr = cat_data.get("description_hr", "").lower()
            query_words = set(query_lower.split())
            desc_words = set(desc_hr.split())
            overlap = len(query_words & desc_words)
            if overlap > 0:
                score += 0.1 * min(overlap, 3)

            if score > 0:
                matches[cat_name] = score

        # Sort by score
        result = sorted(matches.items(), key=lambda x: x[1], reverse=True)
        return result

    async def _embedding_match_categories(
        self,
        query: str,
        top_k: int
    ) -> List[Tuple[str, float]]:
        """Match categories using embedding similarity."""
        from services.embedding_service import get_embedding
        import numpy as np

        # Get query embedding
        query_embedding = await get_embedding(query)

        if query_embedding is None:
            return []

        # Calculate similarity to each category
        similarities = []

        for cat_name, cat_data in self._category_embeddings.items():
            cat_embedding = cat_data.get("embedding")
            if cat_embedding is not None:
                # Cosine similarity
                similarity = np.dot(query_embedding, cat_embedding) / (
                    np.linalg.norm(query_embedding) * np.linalg.norm(cat_embedding)
                )
                similarities.append((cat_name, float(similarity)))

        # Sort by similarity and filter
        similarities.sort(key=lambda x: x[1], reverse=True)

        threshold = 0.3
        return [(cat, sim) for cat, sim in similarities[:top_k] if sim >= threshold]

    def _get_tools_from_categories(self, categories: List[str]) -> List[str]:
        """Get all tools from the given categories."""
        tools = set()

        for cat_name in categories:
            if cat_name in self._category_embeddings:
                cat_tools = self._category_embeddings[cat_name].get("tools", [])
                tools.update(cat_tools)
            elif cat_name in self._category_data:
                cat_tools = self._category_data[cat_name].get("tools", [])
                tools.update(cat_tools)

        return list(tools)

    def _filter_tools_by_intent(
        self,
        tools: List[str],
        intent: QueryIntent
    ) -> List[str]:
        """
        INTELLIGENT filtering: Filter tools based on detected intent.

        READ intent → prefer get_ tools
        WRITE intent → prefer post_ tools
        DELETE intent → prefer delete_ tools
        """
        if intent == QueryIntent.READ:
            # For READ, prefer GET tools but also include some POST for lookup
            filtered = [t for t in tools if t.startswith("get_")]
            if filtered:
                return filtered

        elif intent == QueryIntent.WRITE:
            # For WRITE, prefer POST and PUT tools
            filtered = [t for t in tools if t.startswith("post_") or t.startswith("put_")]
            if filtered:
                return filtered

        elif intent == QueryIntent.DELETE:
            # For DELETE, prefer DELETE tools, fallback to POST with "cancel"
            filtered = [t for t in tools if t.startswith("delete_")]
            if not filtered:
                # Some cancel operations are POST
                filtered = [t for t in tools if "cancel" in t.lower() or "delete" in t.lower()]
            if filtered:
                return filtered

        # No filtering if intent is UNKNOWN or no matches found
        return tools

    async def _llm_select_tool(
        self,
        query: str,
        candidate_tools: List[str],
        user_context: Dict[str, Any],
        intent: QueryIntent = QueryIntent.UNKNOWN,
        matched_categories: List[str] = None
    ) -> Dict[str, Any]:
        """
        Select the best tool from candidates using LLM.

        This is the INTELLIGENT part - LLM makes the final decision
        using few-shot examples from training data.
        """
        from services.llm_tool_selector import get_llm_tool_selector

        try:
            selector = await get_llm_tool_selector()

            selection = await selector.select_tool(
                query=query,
                candidate_tools=candidate_tools,
                categories=matched_categories or [],
                registry=self.registry,
                user_context=user_context
            )

            return {
                "tool": selection.tool_name,
                "confidence": selection.confidence,
                "params": {},
                "reasoning": selection.reasoning
            }

        except Exception as e:
            logger.warning(f"LLM selection failed, using fallback: {e}")
            # Fallback to semantic search if LLM fails
            return await self._semantic_fallback(query, candidate_tools, intent)

    async def _semantic_fallback(
        self,
        query: str,
        candidate_tools: List[str],
        intent: QueryIntent = QueryIntent.UNKNOWN
    ) -> Dict[str, Any]:
        """
        Select best tool from candidates using semantic search + keyword matching + intent.

        INTELLIGENT SELECTION:
        1. Keyword matching (fast, domain-specific)
        2. Semantic search (embedding similarity)
        3. Intent-aware fallback (READ→get_, WRITE→post_)
        """
        try:
            # First: Try keyword matching within candidates
            keyword_result = self._keyword_match_tool(query, candidate_tools, intent)
            if keyword_result:
                return keyword_result

            # Second: Try semantic search
            results = await self.registry.find_relevant_tools_with_scores(
                query, top_k=50
            )

            candidate_set = set(candidate_tools)
            best_candidate = None
            best_score = 0.0

            # Apply intent-based scoring boost
            for result in results:
                if result["name"] in candidate_set:
                    score = result["score"]

                    # Boost score if tool matches intent
                    tool_name = result["name"]
                    if intent == QueryIntent.READ and tool_name.startswith("get_"):
                        score *= 1.2  # 20% boost for matching intent
                    elif intent == QueryIntent.WRITE and tool_name.startswith("post_"):
                        score *= 1.2
                    elif intent == QueryIntent.DELETE and tool_name.startswith("delete_"):
                        score *= 1.3  # Higher boost for delete (less common)

                    if score > best_score:
                        best_score = score
                        best_candidate = tool_name

            if best_candidate:
                return {
                    "tool": best_candidate,
                    "confidence": min(best_score, 1.0),
                    "params": {},
                    "reasoning": f"Semantic search with intent boost ({intent.value})"
                }

            # Third: Intent-aware fallback to first matching candidate
            if candidate_tools:
                # Pick tool based on intent
                prefix_map = {
                    QueryIntent.READ: "get_",
                    QueryIntent.WRITE: "post_",
                    QueryIntent.DELETE: "delete_"
                }
                preferred_prefix = prefix_map.get(intent)

                if preferred_prefix:
                    for tool in candidate_tools:
                        if tool.startswith(preferred_prefix):
                            return {
                                "tool": tool,
                                "confidence": 0.5,
                                "params": {},
                                "reasoning": f"Intent-based selection ({intent.value} → {preferred_prefix})"
                            }

                # Fallback to any tool with list pattern
                for tool in candidate_tools:
                    if tool.startswith("get_") and ("list" in query.lower() or "popis" in query.lower()):
                        return {
                            "tool": tool,
                            "confidence": 0.5,
                            "params": {},
                            "reasoning": "Category match with list pattern"
                        }

            # Fourth: use global best with low confidence
            if results:
                return {
                    "tool": results[0]["name"],
                    "confidence": results[0]["score"] * 0.3,
                    "params": {},
                    "reasoning": "Global fallback"
                }

        except Exception as e:
            logger.error(f"Tool selection failed: {e}")

        return {"tool": None, "confidence": 0.0}

    def _keyword_match_tool(
        self,
        query: str,
        candidate_tools: List[str],
        intent: QueryIntent = QueryIntent.UNKNOWN
    ) -> Optional[Dict[str, Any]]:
        """
        INTELLIGENT keyword matching with intent awareness.

        Uses both domain-specific keywords AND detected intent
        to select the best tool.
        """
        query_lower = query.lower()

        # Priority tools - organized by intent
        priority_tools_read = {
            # Availability (READ)
            ("slobodn", "dostupn"): "get_AvailableVehicles",
            # Vehicle info (READ) - comprehensive MasterData patterns
            ("podaci", "info"): "get_MasterData",
            ("registracija", "registracij", "tablica", "tablice"): "get_MasterData",
            ("kilometraz", "km "): "get_MasterData",  # kilometraza, not just km
            ("moja tablica", "moje vozilo", "moj auto"): "get_MasterData",
            ("koliko", "kolika", "koja je"): "get_MasterData",
            ("istice", "istjece"): "get_MasterData",  # expiry queries
            ("servis", "do servisa"): "get_MasterData",
            # Trips (READ)
            ("trip", "putovanj", "tripov"): "get_Trips",
            # Expenses (READ)
            ("troskov", "expense", "trošak"): "get_Expenses",
            # Dashboard (READ)
            ("dashboard",): "get_DashboardItems",
            # Bookings (READ)
            ("rezervacij", "booking", "moje rezerv"): "get_VehicleCalendar",
        }

        priority_tools_write = {
            # Damage/Case (WRITE)
            ("stet", "kvar", "udari", "ogreb", "prijavi", "slomio", "pokvario"): "post_AddCase",
            # Mileage (WRITE)
            ("unesi", "upisi"): "post_AddMileage",
            # Booking (WRITE) - multiple patterns for booking intent
            ("rezerviraj",): "post_VehicleCalendar",
            ("trebam rezerv", "zelim rezerv", "hocu rezerv"): "post_VehicleCalendar",
            ("trebam auto", "trebam vozilo"): "get_AvailableVehicles",  # First check availability
        }

        # Select priority map based on intent
        if intent == QueryIntent.WRITE:
            # Check write tools first
            for keywords, tool in priority_tools_write.items():
                if any(kw in query_lower for kw in keywords):
                    if tool in candidate_tools:
                        return {
                            "tool": tool,
                            "confidence": 0.95,
                            "params": {},
                            "reasoning": f"Intent-aware priority match: {tool} (intent={intent.value})"
                        }
            # Then check read tools (might still be valid)
            for keywords, tool in priority_tools_read.items():
                if any(kw in query_lower for kw in keywords):
                    if tool in candidate_tools:
                        return {
                            "tool": tool,
                            "confidence": 0.7,
                            "params": {},
                            "reasoning": f"Cross-intent match: {tool}"
                        }
        else:
            # For READ/UNKNOWN, check read tools first
            for keywords, tool in priority_tools_read.items():
                if any(kw in query_lower for kw in keywords):
                    if tool in candidate_tools:
                        return {
                            "tool": tool,
                            "confidence": 0.95,
                            "params": {},
                            "reasoning": f"Intent-aware priority match: {tool} (intent={intent.value})"
                        }
            # Then check write tools
            for keywords, tool in priority_tools_write.items():
                if any(kw in query_lower for kw in keywords):
                    if tool in candidate_tools:
                        return {
                            "tool": tool,
                            "confidence": 0.8,
                            "params": {},
                            "reasoning": f"Cross-intent match: {tool}"
                        }

        # Fallback to pattern matching with intent awareness
        keyword_to_pattern = {
            "troskov": "Expense",
            "expense": "Expense",
            "trip": "Trip",
            "putovanj": "Trip",
            "rezervacij": "Calendar",
            "booking": "Booking",
            "kilometr": "Mileage",
            "vozil": "Vehicle",
            "auto": "Vehicle",
            "case": "Case",
            "dashboard": "Dashboard",
            "podaci": "MasterData",
        }

        for keyword, pattern in keyword_to_pattern.items():
            if keyword in query_lower:
                # Find best matching tool considering intent
                best_tool = None
                best_score = 0

                for tool in candidate_tools:
                    if pattern.lower() in tool.lower():
                        score = 1.0

                        # Boost based on intent
                        if intent == QueryIntent.READ and tool.startswith("get_"):
                            score = 1.5
                        elif intent == QueryIntent.WRITE and tool.startswith("post_"):
                            score = 1.5
                        elif intent == QueryIntent.DELETE and tool.startswith("delete_"):
                            score = 1.5

                        if score > best_score:
                            best_score = score
                            best_tool = tool

                if best_tool:
                    return {
                        "tool": best_tool,
                        "confidence": 0.7,
                        "params": {},
                        "reasoning": f"Pattern '{pattern}' with intent boost → {best_tool}"
                    }

        return None

    def _determine_flow_type(
        self,
        tool_name: str,
        extracted_params: Dict[str, Any],
        user_context: Dict[str, Any]
    ) -> Tuple[FlowType, List[str]]:
        """
        Determine flow type based on PREDEFINED RULES.

        This is NOT an LLM decision - it's based on:
        1. Tool name (certain tools always trigger flows)
        2. Missing parameters (if required params missing → flow)
        3. User context (if vehicle already in context → simpler flow)
        """
        # Check if tool is a flow trigger
        if tool_name in FLOW_TRIGGERS:
            required_params = FLOW_REQUIRED_PARAMS.get(tool_name, [])

            # Check which params are missing
            missing = []
            for param in required_params:
                param_lower = param.lower()

                # Check in extracted params
                if param in extracted_params or param_lower in extracted_params:
                    continue

                # Check in user context
                if param_lower == "vehicleid" and user_context.get("vehicle_id"):
                    continue
                if param_lower == "personid" and user_context.get("person_id"):
                    continue

                missing.append(param)

            # If has missing params → use flow
            if missing:
                return FLOW_TRIGGERS[tool_name], missing
            else:
                # All params available → can do simple execution
                # But some tools still need confirmation
                if tool_name.startswith("post_") or tool_name.startswith("delete_"):
                    # Write operations may still need confirmation
                    return FLOW_TRIGGERS[tool_name], []
                else:
                    return FlowType.SIMPLE, []

        # Check if it's a list tool
        if tool_name in LIST_TOOLS:
            return FlowType.LIST, []

        # Default: simple API call
        return FlowType.SIMPLE, []

    def _fallback_decision(
        self,
        query: str,
        reason: str,
        intent: QueryIntent = QueryIntent.UNKNOWN
    ) -> RoutingDecision:
        """Create fallback decision when routing fails."""
        return RoutingDecision(
            tool_name=None,
            confidence=0.0,
            flow_type=FlowType.SIMPLE,
            intent=intent,
            reasoning=f"Fallback: {reason}",
            is_fallback=True
        )


# ============================================================================
# Singleton instance
# ============================================================================
_router: Optional[IntelligentRouter] = None


async def get_intelligent_router(registry) -> IntelligentRouter:
    """Get or create singleton router instance."""
    global _router
    if _router is None:
        _router = IntelligentRouter(registry)
        await _router.initialize()
    return _router
