"""
20 Diverse Test Questions for Bot Testing
Tests edge cases, ambiguous queries, and various user scenarios.
"""

import asyncio
import sys
sys.path.insert(0, '.')

from services.registry import ToolRegistry
from services.query_router import QueryRouter

# 20 Test Questions - diverse scenarios
TEST_QUESTIONS = [
    # === AVAILABILITY (READ intent) ===
    {
        "query": "ima li slobodnih vozila za sutra",
        "expected_intent": "READ",
        "expected_tools": ["get_AvailableVehicles", "get_VehicleCalendar"],
        "category": "availability"
    },
    {
        "query": "koji auti su dostupni od ponedjeljka do srijede",
        "expected_intent": "READ",
        "expected_tools": ["get_AvailableVehicles"],
        "category": "availability"
    },

    # === DAMAGE/CASE REPORTING (WRITE intent) ===
    {
        "query": "udario sam u stup na parkiralistu",
        "expected_intent": "WRITE",
        "expected_tools": ["post_AddCase", "post_Cases"],
        "category": "damage"
    },
    {
        "query": "netko mi je ogrebao auto dok je bio parkiran",
        "expected_intent": "WRITE",
        "expected_tools": ["post_AddCase"],
        "category": "damage"
    },
    {
        "query": "imam problema s motorom, cuje se cudna buka",
        "expected_intent": "WRITE",
        "expected_tools": ["post_AddCase"],
        "category": "damage"
    },

    # === SERVICE/MAINTENANCE ===
    {
        "query": "koliko jos mogu voziti do servisa",
        "expected_intent": "READ",
        "expected_tools": ["get_MasterData"],
        "category": "service"
    },
    {
        "query": "kad je zadnji put auto bio na servisu",
        "expected_intent": "READ",
        "expected_tools": ["get_MasterData"],
        "category": "service"
    },

    # === MILEAGE ===
    {
        "query": "trenutna kilometraza",
        "expected_intent": "READ",
        "expected_tools": ["get_MasterData", "get_Vehicles_id"],
        "category": "mileage"
    },
    {
        "query": "moram upisati kilometre 45000",
        "expected_intent": "WRITE",
        "expected_tools": ["post_Mileage"],
        "category": "mileage"
    },

    # === BOOKING/RESERVATION ===
    {
        "query": "trebam auto za poslovni put u Zagreb sljedeci tjedan",
        "expected_intent": "WRITE",
        "expected_tools": ["get_AvailableVehicles", "post_VehicleCalendar"],
        "category": "booking"
    },
    {
        "query": "moje rezervacije",
        "expected_intent": "READ",
        "expected_tools": ["get_VehicleCalendar"],
        "category": "booking"
    },
    {
        "query": "otkazi moju rezervaciju za petak",
        "expected_intent": "WRITE",
        "expected_tools": ["delete_VehicleCalendar"],
        "category": "booking"
    },

    # === VEHICLE INFO ===
    {
        "query": "registracija istice za koliko",
        "expected_intent": "READ",
        "expected_tools": ["get_MasterData"],
        "category": "vehicle_info"
    },
    {
        "query": "koji je broj tablice od passata",
        "expected_intent": "READ",
        "expected_tools": ["get_MasterData", "get_Vehicles"],
        "category": "vehicle_info"
    },

    # === AMBIGUOUS/COMPLEX ===
    {
        "query": "golf",
        "expected_intent": "UNKNOWN",
        "expected_tools": [],  # Too ambiguous
        "category": "ambiguous"
    },
    {
        "query": "trebam pomoc",
        "expected_intent": "UNKNOWN",
        "expected_tools": [],
        "category": "ambiguous"
    },

    # === MIXED LANGUAGE ===
    {
        "query": "check availability za sutra",
        "expected_intent": "READ",
        "expected_tools": ["get_AvailableVehicles"],
        "category": "mixed_language"
    },

    # === INFORMAL/TYPOS ===
    {
        "query": "di je moj auto",
        "expected_intent": "READ",
        "expected_tools": ["get_MasterData"],
        "category": "informal"
    },
    {
        "query": "mos mi rec kad imam rezervaciju",
        "expected_intent": "READ",
        "expected_tools": ["get_VehicleCalendar"],
        "category": "informal"
    },

    # === MULTI-STEP ===
    {
        "query": "rezerviraj mi slobodno vozilo za sutra i prijavi da imam ogrebotinu na branicima",
        "expected_intent": "WRITE",
        "expected_tools": ["get_AvailableVehicles", "post_VehicleCalendar", "post_AddCase"],
        "category": "multi_step"
    },
]


async def test_intent_detection():
    """Test intent detection for all questions."""
    from services.patterns import READ_INTENT_PATTERNS, MUTATION_INTENT_PATTERNS
    import re

    print("\n" + "="*60)
    print("TEST 1: Intent Detection")
    print("="*60)

    results = []
    for q in TEST_QUESTIONS:
        query = q["query"].lower().strip()
        expected = q["expected_intent"]

        is_write = any(re.search(p, query) for p in MUTATION_INTENT_PATTERNS)
        is_read = any(re.search(p, query) for p in READ_INTENT_PATTERNS)

        if is_write:
            detected = "WRITE"
        elif is_read:
            detected = "READ"
        else:
            detected = "UNKNOWN"

        match = detected == expected
        results.append(match)
        status = "OK" if match else "FAIL"
        print(f"[{status}] {q['category']:15} | {detected:7} | {query[:45]}")

    passed = sum(results)
    print(f"\nPassed: {passed}/{len(results)} ({100*passed/len(results):.0f}%)")
    return results


async def test_query_router():
    """Test QueryRouter pattern matching."""
    router = QueryRouter()

    print("\n" + "="*60)
    print("TEST 2: QueryRouter Pattern Matching")
    print("="*60)

    for q in TEST_QUESTIONS:
        result = router.route(q["query"])
        status = "MATCH" if result.matched else "NO MATCH"
        tool = result.tool_name or "-"
        print(f"[{status:8}] {q['category']:15} | {tool:25} | {q['query'][:35]}")


async def test_semantic_search():
    """Test semantic search tool selection."""
    registry = ToolRegistry()
    await registry.initialize()

    print("\n" + "="*60)
    print("TEST 3: Semantic Search (Filter-Then-Search)")
    print("="*60)

    for q in TEST_QUESTIONS[:10]:  # First 10 for speed
        results = await registry.find_relevant_tools_with_scores(
            query=q["query"],
            top_k=3,
            use_filtered_search=True
        )

        tools_found = [r["name"] for r in results]
        expected = q["expected_tools"]

        # Check if any expected tool is in results
        hit = any(t in tools_found for t in expected) if expected else True
        status = "HIT" if hit else "MISS"

        print(f"\n[{status}] {q['query'][:50]}")
        print(f"  Expected: {expected}")
        print(f"  Found: {tools_found}")


async def main():
    print("\n" + "="*60)
    print("20 DIVERSE TEST QUESTIONS - BOT TESTING")
    print("="*60)

    # Test 1: Intent detection
    await test_intent_detection()

    # Test 2: QueryRouter
    await test_query_router()

    # Test 3: Semantic search (requires registry initialization)
    # Uncomment when running with full environment:
    # await test_semantic_search()


if __name__ == "__main__":
    asyncio.run(main())
