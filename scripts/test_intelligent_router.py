#!/usr/bin/env python3
"""
Test script for Intelligent Router
===================================
Tests the routing decisions for various query types.

Usage:
    python scripts/test_intelligent_router.py
"""

import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Fix Windows encoding
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


# Test cases: (query, expected_flow_type, expected_tool_pattern)
TEST_CASES = [
    # === GREETINGS (Direct Response) ===
    ("bok", "direct_response", None),
    ("pozdrav", "direct_response", None),
    ("hvala", "direct_response", None),

    # === BOOKING FLOW ===
    ("trebam rezervirati auto za sutra", "booking", "VehicleCalendar|Booking|Available"),
    ("zelim rezervirati vozilo", "booking", "VehicleCalendar|Booking|Available"),
    ("rezerviraj mi auto", "booking", "VehicleCalendar|Booking|Available"),

    # === AVAILABILITY FLOW ===
    ("ima li slobodnih vozila", "availability", "AvailableVehicles"),
    ("koja vozila su dostupna", "availability", "AvailableVehicles"),
    ("slobodna vozila za sutra", "availability", "AvailableVehicles"),

    # === CASE/DAMAGE FLOW ===
    ("udario sam u stup", "case_creation", "AddCase"),
    ("imam stetu na vozilu", "case_creation", "AddCase"),
    ("prijavi kvar", "case_creation", "AddCase"),
    ("ogrebao sam auto", "case_creation", "AddCase"),

    # === MILEAGE INPUT FLOW ===
    ("unesi kilometre 45000", "mileage_input", "AddMileage"),
    ("upisi kilometrazu", "mileage_input", "AddMileage"),

    # === SIMPLE API (Vehicle Info) ===
    ("kolika je moja kilometraza", "simple", "MasterData"),
    ("kad istice registracija", "simple", "MasterData"),
    ("podaci o vozilu", "simple", "MasterData"),
    ("koja je moja tablica", "simple", "MasterData"),

    # === LIST QUERIES ===
    ("moje rezervacije", "list", "VehicleCalendar"),
    ("pokazi moje bookinge", "list", "VehicleCalendar"),

    # === EDGE CASES (should still work) ===
    ("koliko imam do servisa", "simple", "MasterData"),
    ("trebam auto od ponedjeljka do srijede", "booking", "VehicleCalendar|Booking|Available"),

    # === QUERIES NOT IN TRAINING (tests generalization) ===
    ("prikazi sve troskove", "list", "Expense"),  # list is correct - showing list of expenses
    ("dodaj novi trosak", "simple", "Expense"),
    ("obrisi rezervaciju", "simple", "delete"),
]


async def test_router():
    """Test the intelligent router with various queries."""
    from services.registry import ToolRegistry
    from services.intelligent_router import get_intelligent_router, FlowType
    from config import get_settings

    settings = get_settings()

    print("=" * 70)
    print("  INTELLIGENT ROUTER TEST")
    print("=" * 70)

    # Initialize registry
    print("\n[1/3] Initializing Tool Registry...")
    registry = ToolRegistry()
    await registry.initialize(settings.swagger_sources)
    print(f"      Loaded {len(registry.tools)} tools")

    # Initialize router
    print("\n[2/3] Initializing Intelligent Router...")
    router = await get_intelligent_router(registry)
    print(f"      Loaded {len(router._category_data)} categories")

    # Mock user context
    user_context = {
        "person_id": "test-person-123",
        "tenant_id": "test-tenant-456",
        "vehicle_id": None,  # No assigned vehicle
    }

    # Run tests
    print("\n[3/3] Running tests...")
    print("=" * 70)

    passed = 0
    failed = 0
    results = []

    for query, expected_flow, expected_tool_pattern in TEST_CASES:
        try:
            decision = await router.route(query, user_context)

            # Check flow type
            actual_flow = decision.flow_type.value

            flow_ok = actual_flow == expected_flow

            # Check tool (if expected)
            tool_ok = True
            if expected_tool_pattern:
                if decision.tool_name:
                    import re
                    tool_ok = bool(re.search(expected_tool_pattern, decision.tool_name, re.I))
                else:
                    tool_ok = False

            # Overall result
            success = flow_ok and tool_ok

            if success:
                passed += 1
                status = "PASS"
            else:
                failed += 1
                status = "FAIL"

            results.append({
                "query": query,
                "expected_flow": expected_flow,
                "actual_flow": actual_flow,
                "expected_tool": expected_tool_pattern,
                "actual_tool": decision.tool_name,
                "status": status,
                "confidence": decision.confidence,
                "categories": decision.matched_categories,
            })

            # Print result
            icon = "✓" if success else "✗"
            print(f"\n{icon} Query: \"{query}\"")
            print(f"  Flow: {actual_flow} {'✓' if flow_ok else '✗'} (expected: {expected_flow})")
            if expected_tool_pattern:
                print(f"  Tool: {decision.tool_name or 'None'} {'✓' if tool_ok else '✗'} (expected: ~{expected_tool_pattern})")
            print(f"  Categories: {decision.matched_categories}")
            print(f"  Confidence: {decision.confidence:.2f}")

        except Exception as e:
            failed += 1
            print(f"\n✗ Query: \"{query}\"")
            print(f"  ERROR: {e}")

    # Summary
    print("\n" + "=" * 70)
    print(f"  RESULTS: {passed}/{len(TEST_CASES)} passed ({passed/len(TEST_CASES)*100:.0f}%)")
    print("=" * 70)

    # Show failures
    failures = [r for r in results if r["status"] == "FAIL"]
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - \"{f['query']}\"")
            print(f"    Expected: {f['expected_flow']}, Got: {f['actual_flow']}")
            print(f"    Tool: {f['actual_tool']} (expected ~{f['expected_tool']})")

    return passed, failed


async def test_category_coverage():
    """Test that all major use cases have category coverage."""
    from services.registry import ToolRegistry
    from services.intelligent_router import get_intelligent_router
    from config import get_settings

    settings = get_settings()

    print("\n" + "=" * 70)
    print("  CATEGORY COVERAGE TEST")
    print("=" * 70)

    registry = ToolRegistry()
    await registry.initialize(settings.swagger_sources)

    router = await get_intelligent_router(registry)

    # Test diverse queries to see category matching
    test_queries = [
        "ima li slobodnih vozila",
        "prijavi stetu",
        "unesi kilometre",
        "moje rezervacije",
        "podaci o vozilu",
        "obrisi expense",
        "dodaj novi trip",
        "pokazi dashboard",
    ]

    print("\nCategory matching for diverse queries:\n")

    for query in test_queries:
        categories = await router._find_relevant_categories(query, top_k=3)
        print(f"  \"{query}\"")
        print(f"    → Categories: {categories}")
        print()


async def main():
    """Main entry point."""
    try:
        # Run router tests
        passed, failed = await test_router()

        # Run category coverage test
        await test_category_coverage()

        # Exit with appropriate code
        sys.exit(0 if failed == 0 else 1)

    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
