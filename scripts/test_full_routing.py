#!/usr/bin/env python3
"""
Full Routing Test - Tests the complete routing pipeline.
Tests: QueryRouter → IntelligentRouter → Flow/API decision

Usage:
    python scripts/test_full_routing.py
"""

import asyncio
import sys
import os
import io

# Fix Windows encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Test cases with expected outcomes
# Note: "router" can be "deterministic" or "intelligent" or "any" (either is OK)
TEST_CASES = [
    # === DETERMINISTIC (QueryRouter should match) ===
    ("bok", "deterministic", "direct_response", None),
    ("koliko je km", "deterministic", "simple", "get_MasterData"),
    ("moje rezervacije", "deterministic", "list", "get_VehicleCalendar"),

    # === CAN BE EITHER (both routers work) ===
    ("kad istice registracija", "any", "simple", "get_MasterData"),

    # === INTELLIGENT ROUTING (QueryRouter miss, IntelligentRouter handles) ===
    ("daj mi popis troskova", "intelligent", "list", "Expense"),  # list flow is correct for get_Expenses
    ("pokazi mi sve tripove", "intelligent", "list", "Trip"),      # list flow is correct for get_Trips
    ("zelim vidjeti dashboard", "intelligent", "simple", "Dashboard"),

    # === FLOWS (deterministic router handles these well!) ===
    ("trebam rezervirati auto", "any", "booking", "VehicleCalendar|Booking|Available"),
    ("udario sam u stup", "any", "case_creation", "AddCase"),
]


async def test_routing_pipeline():
    """Test the full routing pipeline."""
    from services.query_router import get_query_router
    from services.intelligent_router import IntelligentRouter, FlowType
    from services.registry import ToolRegistry
    from config import get_settings

    settings = get_settings()

    print("=" * 70)
    print("  FULL ROUTING PIPELINE TEST")
    print("=" * 70)

    # Initialize components
    print("\n[1/4] Initializing Tool Registry...")
    registry = ToolRegistry()
    await registry.initialize(settings.swagger_sources)
    print(f"      Loaded {len(registry.tools)} tools")

    print("\n[2/4] Initializing Query Router...")
    query_router = get_query_router()
    print(f"      Loaded {len(query_router.rules)} rules")

    print("\n[3/4] Initializing Intelligent Router...")
    intelligent_router = IntelligentRouter(registry)
    await intelligent_router.initialize()
    print(f"      Loaded {len(intelligent_router._category_data)} categories")

    # Mock user context
    user_context = {
        "person_id": "test-person-123",
        "tenant_id": "test-tenant-456",
        "vehicle_id": None,
    }

    # Run tests
    print("\n[4/4] Running routing tests...")
    print("=" * 70)

    passed = 0
    failed = 0

    for query, expected_router, expected_flow, expected_tool_pattern in TEST_CASES:
        print(f"\n{'─' * 60}")
        print(f"QUERY: \"{query}\"")
        print(f"EXPECTED: router={expected_router}, flow={expected_flow}, tool~={expected_tool_pattern}")

        # Step 1: Try QueryRouter (deterministic)
        route = query_router.route(query, user_context)

        if route.matched:
            actual_router = "deterministic"
            actual_flow = route.flow_type
            actual_tool = route.tool_name
        else:
            # Step 2: Try IntelligentRouter
            decision = await intelligent_router.route(query, user_context)
            actual_router = "intelligent"
            actual_flow = decision.flow_type.value
            actual_tool = decision.tool_name

        # Check results
        import re

        # Router check: "any" means either is OK
        router_ok = (
            expected_router == "any" or
            actual_router == expected_router
        )
        flow_ok = actual_flow == expected_flow

        tool_ok = True
        if expected_tool_pattern:
            if actual_tool:
                tool_ok = bool(re.search(expected_tool_pattern, actual_tool, re.I))
            else:
                tool_ok = False

        success = router_ok and flow_ok and tool_ok

        if success:
            passed += 1
            status = "PASS"
        else:
            failed += 1
            status = "FAIL"

        print(f"ACTUAL:   router={actual_router}, flow={actual_flow}, tool={actual_tool}")
        print(f"RESULT:   {status}")

        if not success:
            if not router_ok:
                print(f"  ✗ Router mismatch: expected {expected_router}, got {actual_router}")
            if not flow_ok:
                print(f"  ✗ Flow mismatch: expected {expected_flow}, got {actual_flow}")
            if not tool_ok:
                print(f"  ✗ Tool mismatch: expected ~{expected_tool_pattern}, got {actual_tool}")

    # Summary
    print("\n" + "=" * 70)
    print(f"  RESULTS: {passed}/{len(TEST_CASES)} passed ({passed/len(TEST_CASES)*100:.0f}%)")
    print("=" * 70)

    return passed, failed


async def test_category_to_tool_flow():
    """Test that categories lead to correct tools."""
    from services.intelligent_router import IntelligentRouter
    from services.registry import ToolRegistry
    from config import get_settings

    settings = get_settings()

    print("\n" + "=" * 70)
    print("  CATEGORY → TOOL FLOW TEST")
    print("=" * 70)

    registry = ToolRegistry()
    await registry.initialize(settings.swagger_sources)

    router = IntelligentRouter(registry)
    await router.initialize()

    user_context = {"person_id": "test", "tenant_id": "test"}

    # Test diverse queries
    test_queries = [
        ("ima li slobodnih vozila", "get_AvailableVehicles"),
        ("prijavi stetu na autu", "post_AddCase"),
        ("unesi kilometre 45000", "post_AddMileage"),
        ("podaci o mom vozilu", "get_MasterData"),
    ]

    print("\nTesting category→tool resolution:\n")

    for query, expected_tool in test_queries:
        decision = await router.route(query, user_context)

        categories = decision.matched_categories
        tool = decision.tool_name

        tool_match = expected_tool.lower() in (tool or "").lower()

        status = "✓" if tool_match else "✗"
        print(f"{status} \"{query}\"")
        print(f"   Categories: {categories}")
        print(f"   Tool: {tool} (expected: {expected_tool})")
        print()


async def main():
    """Main entry point."""
    try:
        # Run routing tests
        passed, failed = await test_routing_pipeline()

        # Run category-to-tool flow test
        await test_category_to_tool_flow()

        # Exit with appropriate code
        sys.exit(0 if failed == 0 else 1)

    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
