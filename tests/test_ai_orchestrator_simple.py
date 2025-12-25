"""
Simplified Integration Tests for ai_orchestrator.py v12.2 Fixes

Tests CRITICAL fixes that don't require full AIOrchestrator:
1. Entity extraction data loss fix (CRITICAL FIX 3)
2. KeyError crash fix in role_counts (CRITICAL FIX 4)
3. Entity formatting with lists (CRITICAL FIX 3)
"""
import re
from collections import Counter
from typing import Dict, List


# Minimal implementations copied from fixed ai_orchestrator.py
def _extract_entities(messages: List[Dict[str, str]]) -> Dict[str, List[str]]:
    """Copy of fixed _extract_entities method."""
    entities = {
        "VehicleId": [],
        "PersonId": [],
        "BookingId": [],
        "LicencePlate": []
    }
    uuid_pattern = r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})'
    plate_pattern = r'([A-ZČĆŽŠĐ]{2}[\s\-]?\d{3,4}[\s\-]?[A-ZČĆŽŠĐ]{1,2})'

    for msg in messages:
        content = msg.get("content", "")
        if not content:
            continue

        # PERFORMANCE FIX: Compute content_lower once per message
        content_lower = content.lower()

        uuids = re.findall(uuid_pattern, content_lower)
        for uuid in uuids:
            # CRITICAL FIX: Append to list instead of overwriting
            if "vehicle" in content_lower or "vozil" in content_lower:
                entities["VehicleId"].append(uuid)
            elif "person" in content_lower or "osob" in content_lower:
                entities["PersonId"].append(uuid)
            elif "booking" in content_lower or "rezerv" in content_lower:
                entities["BookingId"].append(uuid)

        plates = re.findall(plate_pattern, content.upper())
        # CRITICAL FIX: Append all plates, not just last one
        entities["LicencePlate"].extend(plates)

    return entities


def _format_entity_context(entities: Dict[str, List[str]]) -> str:
    """Copy of fixed _format_entity_context method."""
    parts = []
    for key, values in entities.items():
        if values:  # Only include non-empty lists
            parts.append(f"{key}={','.join(values)}")
    return ", ".join(parts)


def _summarize_conversation(messages: List[Dict[str, str]]) -> str:
    """Copy of fixed _summarize_conversation method."""
    entities = _extract_entities(messages)
    summary_parts = []

    if entities:
        formatted = _format_entity_context(entities)
        if formatted:
            summary_parts.append(f"Ranije entiteti: {formatted}")

    role_counts = Counter(m.get("role") for m in messages)
    # CRITICAL FIX: Use .get() to prevent KeyError
    summary_parts.append(
        f"Prethodnih {len(messages)} poruka "
        f"({role_counts.get('user', 0)} user, {role_counts.get('assistant', 0)} assistant, {role_counts.get('tool', 0)} tool calls)"
    )

    return ". ".join(summary_parts)


# ============================================================================
# TESTS
# ============================================================================

def test_multiple_uuids():
    """
    CRITICAL FIX 3: Multiple UUIDs
    User message contains 2+ UUIDs - ensure ALL are captured, not just last one.
    """
    print("=" * 60)
    print("TEST: Multiple UUIDs (CRITICAL FIX 3)")
    print("=" * 60)

    # Message with 2 vehicle UUIDs
    messages = [
        {
            "role": "user",
            "content": "Transfer from vehicle 123e4567-e89b-12d3-a456-426614174000 to 234f5678-f90a-12d3-a456-426614174001"
        }
    ]

    entities = _extract_entities(messages)

    print(f"Message: {messages[0]['content'][:80]}...")
    print(f"Extracted VehicleIds: {entities['VehicleId']}")

    # VALIDATION
    assert len(entities["VehicleId"]) == 2, f"Expected 2 UUIDs, got {len(entities['VehicleId'])}"
    assert "123e4567-e89b-12d3-a456-426614174000" in entities["VehicleId"]
    assert "234f5678-f90a-12d3-a456-426614174001" in entities["VehicleId"]

    print("[OK] Both UUIDs captured!")
    print()


def test_no_tool_calls_keyerror():
    """
    CRITICAL FIX 4: KeyError in role_counts
    Conversation with only user + assistant (no tool role) should not crash.
    """
    print("=" * 60)
    print("TEST: No Tool Calls - KeyError Fix (CRITICAL FIX 4)")
    print("=" * 60)

    # Conversation without tool calls
    messages = [
        {"role": "user", "content": "Što mogu napraviti?"},
        {"role": "assistant", "content": "Mogu provjeriti vozila."},
        {"role": "user", "content": "Ok, hvala."}
    ]

    try:
        summary = _summarize_conversation(messages)
        print(f"Summary: {summary}")
        print("[OK] No crash! KeyError fix working.")

        # Verify "0 tool calls" is mentioned
        assert "0 tool" in summary.lower(), "Expected '0 tool calls' in summary"
        print("[OK] Correctly shows '0 tool calls'")

    except KeyError as e:
        print(f"[FAIL] KeyError crash: {e}")
        raise

    print()


def test_entity_formatting_with_lists():
    """
    CRITICAL FIX 3: Entity Formatting with Lists
    _format_entity_context should handle lists correctly.
    """
    print("=" * 60)
    print("TEST: Entity Formatting with Lists (CRITICAL FIX 3)")
    print("=" * 60)

    entities = {
        "VehicleId": ["uuid-1", "uuid-2"],
        "PersonId": ["uuid-3"],
        "BookingId": [],
        "LicencePlate": ["ZG-123-AB", "ZG-456-CD"]
    }

    formatted = _format_entity_context(entities)

    print(f"Entities: {entities}")
    print(f"Formatted: {formatted}")

    # VALIDATION
    assert "VehicleId=uuid-1,uuid-2" in formatted
    assert "PersonId=uuid-3" in formatted
    assert "BookingId" not in formatted  # Empty list should be excluded
    assert "LicencePlate=ZG-123-AB,ZG-456-CD" in formatted

    print("[OK] Lists formatted correctly!")
    print()


def test_multiple_plates():
    """
    CRITICAL FIX 3: Multiple License Plates
    Ensure all plates are captured, not just the last one.
    """
    print("=" * 60)
    print("TEST: Multiple License Plates (CRITICAL FIX 3)")
    print("=" * 60)

    messages = [
        {
            "role": "user",
            "content": "Prebaci sa ZG-123-AB na ZG-456-CD i onda na ST-789-EF"
        }
    ]

    entities = _extract_entities(messages)

    print(f"Message: {messages[0]['content']}")
    print(f"Extracted plates: {entities['LicencePlate']}")

    # VALIDATION
    assert len(entities["LicencePlate"]) == 3, f"Expected 3 plates, got {len(entities['LicencePlate'])}"
    assert "ZG-123-AB" in entities["LicencePlate"]
    assert "ZG-456-CD" in entities["LicencePlate"]
    assert "ST-789-EF" in entities["LicencePlate"]

    print("[OK] All plates captured!")
    print()


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("AI ORCHESTRATOR v12.2 - SIMPLIFIED INTEGRATION TESTS")
    print("=" * 60)
    print()

    test_multiple_uuids()
    test_no_tool_calls_keyerror()
    test_entity_formatting_with_lists()
    test_multiple_plates()

    print("=" * 60)
    print("[SUCCESS] All tests passed!")
    print("=" * 60)
    print()
    print("NOTE: Full integration tests (token budgeting, system prompts)")
    print("      require running application with Azure OpenAI configured.")
