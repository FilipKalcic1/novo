#!/usr/bin/env python3
"""
Test Intent Detection - Verifies intelligent READ/WRITE detection.

This tests the INTELLIGENT part of the router - detecting user intent
from Croatian and English queries using comprehensive regex patterns.

Usage:
    python scripts/test_intent_detection.py
"""

import sys
import os

# Fix Windows encoding
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.intelligent_router import detect_intent, QueryIntent


# Test cases: (query, expected_intent)
TEST_CASES = [
    # === READ INTENT ===
    ("koliko imam kilometara", QueryIntent.READ),
    ("koja je moja kilometraza", QueryIntent.READ),
    ("pokazi mi moje rezervacije", QueryIntent.READ),
    ("kad istice registracija", QueryIntent.READ),
    ("koja vozila su slobodna", QueryIntent.READ),
    ("daj mi popis troskova", QueryIntent.READ),
    ("sto je moja tablica", QueryIntent.READ),
    ("provjeri status slucaja", QueryIntent.READ),
    ("moje rezervacije", QueryIntent.READ),
    ("informacije o vozilu", QueryIntent.READ),

    # === WRITE INTENT ===
    ("unesi kilometre 45000", QueryIntent.WRITE),
    ("upisi kilometrazu", QueryIntent.WRITE),
    ("prijavi stetu", QueryIntent.WRITE),
    ("dodaj novi trosak", QueryIntent.WRITE),
    ("kreiraj rezervaciju", QueryIntent.WRITE),
    ("udario sam u stup", QueryIntent.WRITE),
    ("imam kvar na autu", QueryIntent.WRITE),
    ("trebam rezervirati auto", QueryIntent.WRITE),
    ("ogrebao sam auto", QueryIntent.WRITE),
    ("zelim prijaviti stetu", QueryIntent.WRITE),

    # === DELETE INTENT ===
    ("obrisi rezervaciju", QueryIntent.DELETE),
    ("otkazi booking", QueryIntent.DELETE),
    ("ponisti slucaj", QueryIntent.DELETE),
    ("ukloni trosak", QueryIntent.DELETE),
    ("cancel my booking", QueryIntent.DELETE),

    # === EDGE CASES ===
    ("auto", QueryIntent.READ),  # Default to READ (safer)
    ("vozilo", QueryIntent.READ),
    ("bok", QueryIntent.READ),  # Greeting - defaults to READ
]


def test_intent_detection():
    """Test intent detection on various queries."""
    print("=" * 70)
    print("  INTENT DETECTION TEST")
    print("=" * 70)

    passed = 0
    failed = 0

    print("\n{:<45} | {:<10} | {:<10} | {}".format(
        "Query", "Expected", "Actual", "Status"
    ))
    print("-" * 80)

    for query, expected_intent in TEST_CASES:
        actual_intent = detect_intent(query)
        success = actual_intent == expected_intent

        if success:
            passed += 1
            status = "PASS"
        else:
            failed += 1
            status = "FAIL"

        # Truncate query for display
        display_query = query[:42] + "..." if len(query) > 45 else query

        print("{:<45} | {:<10} | {:<10} | {}".format(
            display_query,
            expected_intent.value,
            actual_intent.value,
            status
        ))

    print("\n" + "=" * 70)
    print(f"  RESULTS: {passed}/{len(TEST_CASES)} passed ({passed/len(TEST_CASES)*100:.0f}%)")
    print("=" * 70)

    if failed > 0:
        print("\nFAILURES:")
        for query, expected in TEST_CASES:
            actual = detect_intent(query)
            if actual != expected:
                print(f"  - \"{query}\"")
                print(f"    Expected: {expected.value}, Got: {actual.value}")

    return passed, failed


def test_intent_coverage():
    """Test that intent patterns cover common Croatian phrases."""
    print("\n" + "=" * 70)
    print("  INTENT PATTERN COVERAGE TEST")
    print("=" * 70)

    # Croatian phrases that should definitely be detected
    must_detect_read = [
        "koja je",
        "koliko je",
        "pokazi mi",
        "daj mi",
        "moje vozilo",
        "slobodna vozila",
    ]

    must_detect_write = [
        "prijavi stetu",
        "unesi km",
        "dodaj",
        "kreiraj",
        "udario sam",
        "imam kvar",
    ]

    must_detect_delete = [
        "obrisi",
        "otkazi",
        "ponisti",
        "ukloni",
    ]

    print("\nTesting READ detection coverage:")
    for phrase in must_detect_read:
        intent = detect_intent(phrase)
        status = "OK" if intent == QueryIntent.READ else f"WRONG ({intent.value})"
        print(f"  '{phrase}' -> {status}")

    print("\nTesting WRITE detection coverage:")
    for phrase in must_detect_write:
        intent = detect_intent(phrase)
        status = "OK" if intent == QueryIntent.WRITE else f"WRONG ({intent.value})"
        print(f"  '{phrase}' -> {status}")

    print("\nTesting DELETE detection coverage:")
    for phrase in must_detect_delete:
        intent = detect_intent(phrase)
        status = "OK" if intent == QueryIntent.DELETE else f"WRONG ({intent.value})"
        print(f"  '{phrase}' -> {status}")


def main():
    """Main entry point."""
    try:
        passed, failed = test_intent_detection()
        test_intent_coverage()

        sys.exit(0 if failed == 0 else 1)

    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
