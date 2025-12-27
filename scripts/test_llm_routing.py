#!/usr/bin/env python3
"""
Test LLM-based Tool Selection

Tests that LLM correctly selects tools with real confidence scores.
This is the TRUE intelligent routing test.

Usage:
    python scripts/test_llm_routing.py
"""

import asyncio
import sys
import os

# Fix Windows encoding
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Test cases: (query, expected_tool_pattern, description)
# Pattern can match any part of tool name (case insensitive)
TEST_CASES = [
    # === VEHICLE INFO ===
    # Both MasterData and Mileage-specific tools are acceptable
    ("koliko imam kilometara", "MasterData|Mileage", "Mileage question"),
    ("koja je moja kilometraza", "MasterData|Mileage", "Mileage question"),
    ("kad istice registracija", "MasterData|Vehicle", "Registration expiry"),
    ("koja je moja tablica", "MasterData|Vehicle", "License plate"),
    ("podaci o vozilu", "MasterData|Vehicle", "Vehicle data"),

    # === BOOKING ===
    ("trebam rezervirati auto", "VehicleCalendar|Booking|Available", "Booking request"),
    ("rezerviraj mi auto za sutra", "VehicleCalendar|Booking", "Booking request"),
    ("moje rezervacije", "get_VehicleCalendar", "My bookings"),

    # === AVAILABILITY ===
    ("ima li slobodnih vozila", "AvailableVehicles", "Availability check"),
    ("koja vozila su dostupna", "AvailableVehicles", "Availability check"),

    # === CASE/DAMAGE - MUST be post_AddCase ===
    ("udario sam u stup", "post_AddCase", "Damage report"),
    ("prijavi stetu", "post_AddCase", "Damage report"),
    ("imam kvar na autu", "post_AddCase", "Damage report"),
    ("ogrebao sam auto", "post_AddCase", "Damage report"),

    # === MILEAGE INPUT ===
    ("unesi kilometre 45000", "post_AddMileage", "Mileage input"),
    ("upisi kilometrazu", "post_AddMileage", "Mileage input"),

    # === EXPENSES ===
    ("pokazi mi troskove", "Expense", "Expenses list"),  # Matches Expense, Expenses, ExpenseGroups
    ("popis troskova", "Expense", "Expenses list"),

    # === TRIPS ===
    ("moji tripovi", "Trips", "Trips list"),
    ("pokazi putovanja", "Trips", "Trips list"),
]


async def test_llm_routing():
    """Test LLM-based tool selection."""
    from services.registry import ToolRegistry
    from services.intelligent_router import IntelligentRouter
    from config import get_settings
    import re

    settings = get_settings()

    print("=" * 70)
    print("  LLM-BASED TOOL SELECTION TEST")
    print("=" * 70)

    # Initialize
    print("\n[1/3] Initializing Tool Registry...")
    registry = ToolRegistry()
    await registry.initialize(settings.swagger_sources)
    print(f"      Loaded {len(registry.tools)} tools")

    print("\n[2/3] Initializing Intelligent Router with LLM...")
    router = IntelligentRouter(registry)
    await router.initialize()
    print(f"      Loaded {len(router._category_data)} categories")

    # Mock user context
    user_context = {
        "person_id": "test-person-123",
        "tenant_id": "test-tenant-456",
        "vehicle_id": None,
    }

    # Run tests
    print("\n[3/3] Running LLM routing tests...")
    print("=" * 70)

    passed = 0
    failed = 0
    results = []

    for query, expected_pattern, description in TEST_CASES:
        print(f"\n{'─' * 60}")
        print(f"QUERY: \"{query}\"")
        print(f"DESC:  {description}")
        print(f"EXPECTED: ~{expected_pattern}")

        try:
            decision = await router.route(query, user_context)

            tool = decision.tool_name or "None"
            confidence = decision.confidence
            reasoning = decision.reasoning[:50] if decision.reasoning else ""

            # Check if tool matches expected pattern
            tool_ok = bool(re.search(expected_pattern, tool, re.I))

            if tool_ok:
                passed += 1
                status = "PASS"
            else:
                failed += 1
                status = "FAIL"

            print(f"ACTUAL:   {tool}")
            print(f"CONF:     {confidence:.2f}")
            print(f"REASON:   {reasoning}...")
            print(f"RESULT:   {status}")

            results.append({
                "query": query,
                "expected": expected_pattern,
                "actual": tool,
                "confidence": confidence,
                "status": status,
            })

        except Exception as e:
            failed += 1
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()

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
            print(f"    Expected: ~{f['expected']}")
            print(f"    Actual: {f['actual']} (conf={f['confidence']:.2f})")

    # Show confidence distribution
    print("\nCONFIDENCE DISTRIBUTION:")
    confs = [r["confidence"] for r in results]
    if confs:
        print(f"  Min: {min(confs):.2f}")
        print(f"  Max: {max(confs):.2f}")
        print(f"  Avg: {sum(confs)/len(confs):.2f}")

    return passed, failed


async def main():
    """Main entry point."""
    try:
        passed, failed = await test_llm_routing()
        sys.exit(0 if failed == 0 else 1)

    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
