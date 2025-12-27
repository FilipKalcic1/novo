#!/usr/bin/env python3
"""
Test Unified Router with REAL scenarios from production logs.

These are actual user messages that caused problems.

Usage:
    python scripts/test_unified_router.py
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


# REAL scenarios from production logs
# Format: (query, current_flow_state, expected_action, expected_tool, description)
REAL_SCENARIOS = [
    # === SCENARIO 1: User in booking flow wants vehicle info ===
    # Note: exit_flow OR simple_api both acceptable - user wants different info
    (
        "Mozes li mi reci moju registraciju",
        {"flow": "booking", "state": "gathering", "missing_params": ["from", "to"], "tool": "get_AvailableVehicles"},
        "exit_flow|simple_api",  # Either exit OR direct answer is acceptable
        "get_MasterData",
        "User in booking flow asks for registration - should handle new request"
    ),

    # === SCENARIO 2: User explicitly says "ne želim" ===
    (
        "Ne želim rezervirati hoću svoj broj registracije i naziv mog vozila",
        {"flow": "booking", "state": "gathering", "missing_params": ["from", "to"], "tool": "get_AvailableVehicles"},
        "exit_flow",
        None,
        "User says 'ne želim rezervirati' - should EXIT flow"
    ),

    # === SCENARIO 3: User provides dates while in booking flow ===
    (
        "Sutra od 9 do 18",
        {"flow": "booking", "state": "gathering", "missing_params": ["from", "to"], "tool": "get_AvailableVehicles"},
        "continue_flow",
        None,
        "User provides dates in booking flow - should CONTINUE flow"
    ),

    # === SCENARIO 4: User wants to enter mileage (WRITE intent) ===
    (
        "Mogu li upisati kilometražu",
        None,  # No active flow
        "start_flow",
        "post_AddMileage",
        "User wants to ENTER mileage - should be WRITE not READ"
    ),

    # === SCENARIO 5: User asks about mileage (READ intent) ===
    (
        "Kolika je moja kilometraža",
        None,
        "simple_api",
        "get_MasterData",  # or get_MileageReports, both acceptable
        "User asks ABOUT mileage - should be READ"
    ),

    # === SCENARIO 6: New booking request ===
    (
        "Trebam rezervirati auto",
        None,
        "start_flow",
        "get_AvailableVehicles",
        "New booking request"
    ),

    # === SCENARIO 7: Report damage ===
    (
        "Udario sam u stup",
        None,
        "start_flow",
        "post_AddCase",
        "Damage report - should start case flow"
    ),

    # === SCENARIO 8: Simple vehicle info request ===
    (
        "Koja je moja tablica",
        None,
        "simple_api",
        "get_MasterData",
        "Simple vehicle info query"
    ),

    # === SCENARIO 9: Greeting ===
    (
        "Bok",
        None,
        "direct_response",
        None,
        "Greeting should get direct response"
    ),

    # === SCENARIO 10: User wants to check available vehicles ===
    (
        "Ima li slobodnih vozila",
        None,
        "start_flow|simple_api",  # Both acceptable
        "get_AvailableVehicles",
        "Availability check"
    ),

    # === SCENARIO 11: User in mileage flow, provides value ===
    (
        "14500",
        {"flow": "mileage", "state": "gathering", "missing_params": ["Value"], "tool": "post_AddMileage"},
        "continue_flow",
        None,
        "User provides mileage value - should continue flow"
    ),

    # === SCENARIO 12: User wants to cancel during confirmation ===
    (
        "Odustani",
        {"flow": "booking", "state": "confirming", "missing_params": [], "tool": "post_VehicleCalendar"},
        "exit_flow",
        None,
        "User says 'odustani' - should exit flow"
    ),
]


async def test_unified_router():
    """Test unified router with real scenarios."""
    from services.unified_router import get_unified_router

    print("=" * 70)
    print("  UNIFIED ROUTER TEST - REAL PRODUCTION SCENARIOS")
    print("=" * 70)

    router = await get_unified_router()

    user_context = {
        "person_id": "test-123",
        "tenant_id": "test-tenant",
        "vehicle": {
            "id": "vehicle-123",
            "name": "Škoda Octavia",
            "plate": "ZG1234AB"
        }
    }

    passed = 0
    failed = 0

    for query, flow_state, expected_action, expected_tool, description in REAL_SCENARIOS:
        print(f"\n{'─' * 60}")
        print(f"SCENARIO: {description}")
        print(f"QUERY: \"{query}\"")
        if flow_state:
            print(f"FLOW STATE: {flow_state.get('flow')} ({flow_state.get('state')})")
        else:
            print("FLOW STATE: None (idle)")
        print(f"EXPECTED: action={expected_action}, tool={expected_tool}")

        try:
            decision = await router.route(query, user_context, flow_state)

            # Check action (may have multiple acceptable values separated by |)
            acceptable_actions = expected_action.split("|")
            action_ok = decision.action in acceptable_actions

            # Check tool (if expected)
            tool_ok = True
            if expected_tool:
                # Allow some flexibility - MasterData or MileageReports both acceptable for mileage READ
                if expected_tool == "get_MasterData" and "Mileage" in (decision.tool or ""):
                    tool_ok = True
                elif expected_tool == "get_AvailableVehicles" and decision.action in ["start_flow", "simple_api"]:
                    tool_ok = decision.tool == expected_tool or decision.tool is None
                else:
                    tool_ok = decision.tool == expected_tool

            success = action_ok and tool_ok

            if success:
                passed += 1
                status = "PASS"
            else:
                failed += 1
                status = "FAIL"

            print(f"ACTUAL: action={decision.action}, tool={decision.tool}")
            print(f"REASONING: {decision.reasoning[:50]}...")
            print(f"RESULT: {status}")

            if not success:
                if not action_ok:
                    print(f"  ✗ Action mismatch: expected {expected_action}, got {decision.action}")
                if not tool_ok:
                    print(f"  ✗ Tool mismatch: expected {expected_tool}, got {decision.tool}")

        except Exception as e:
            failed += 1
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()

    # Summary
    print("\n" + "=" * 70)
    print(f"  RESULTS: {passed}/{len(REAL_SCENARIOS)} passed ({passed/len(REAL_SCENARIOS)*100:.0f}%)")
    print("=" * 70)

    return passed, failed


async def main():
    """Main entry point."""
    try:
        passed, failed = await test_unified_router()
        sys.exit(0 if failed == 0 else 1)

    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
