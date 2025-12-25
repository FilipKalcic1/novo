"""
Tests for centralized pattern registry.
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.patterns import PatternRegistry, normalize_context_key, CONTEXT_KEY_CANONICAL


def test_uuid_detection():
    """Test UUID pattern matching."""
    print("=" * 60)
    print("TEST: UUID Detection")
    print("=" * 60)

    # Valid UUIDs
    valid_uuids = [
        "123e4567-e89b-12d3-a456-426614174000",
        "550E8400-E29B-41D4-A716-446655440000",  # uppercase
        "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    ]

    for uuid in valid_uuids:
        assert PatternRegistry.is_uuid(uuid), f"Should recognize: {uuid}"
    print(f"[OK] {len(valid_uuids)} valid UUIDs recognized")

    # Invalid UUIDs
    invalid_uuids = [
        "not-a-uuid",
        "123456789",
        "123e4567-e89b-12d3-a456",  # too short
    ]

    for uuid in invalid_uuids:
        assert not PatternRegistry.is_uuid(uuid), f"Should reject: {uuid}"
    print(f"[OK] {len(invalid_uuids)} invalid UUIDs rejected")

    # Find UUIDs in text
    text = "Transfer from vehicle 123e4567-e89b-12d3-a456-426614174000 to 550e8400-e29b-41d4-a716-446655440000"
    found = PatternRegistry.find_uuids(text)
    assert len(found) == 2, f"Expected 2 UUIDs, got {len(found)}"
    print(f"[OK] Found {len(found)} UUIDs in text")
    print()


def test_plate_detection():
    """Test Croatian license plate matching."""
    print("=" * 60)
    print("TEST: Croatian License Plate Detection")
    print("=" * 60)

    # Valid plates
    valid_plates = [
        "ZG-1234-AB",
        "ZG 1234 AB",
        "ZG1234AB",
        "ST-789-EF",
        "RI-456-CD",
    ]

    for plate in valid_plates:
        assert PatternRegistry.is_croatian_plate(plate), f"Should recognize: {plate}"
    print(f"[OK] {len(valid_plates)} valid plates recognized")

    # Find plates in text
    text = "Prebaci sa ZG-123-AB na ZG-456-CD i onda na ST-789-EF"
    found = PatternRegistry.find_plates(text)
    assert len(found) == 3, f"Expected 3 plates, got {len(found)}"
    print(f"[OK] Found {len(found)} plates in text: {found}")
    print()


def test_context_key_normalization():
    """Test parameter name normalization."""
    print("=" * 60)
    print("TEST: Context Key Normalization")
    print("=" * 60)

    # Person ID variations
    person_variations = [
        "personId", "PersonId", "PERSONID", "person_id",
        "driverId", "DriverId", "userId", "AssignedToId",
    ]

    for var in person_variations:
        result = normalize_context_key(var)
        assert result == "person_id", f"{var} should normalize to person_id, got {result}"
    print(f"[OK] {len(person_variations)} person_id variations normalized")

    # Vehicle ID variations
    vehicle_variations = [
        "vehicleId", "VehicleId", "vehicle_id", "carId",
    ]

    for var in vehicle_variations:
        result = normalize_context_key(var)
        assert result == "vehicle_id", f"{var} should normalize to vehicle_id, got {result}"
    print(f"[OK] {len(vehicle_variations)} vehicle_id variations normalized")

    # Unknown parameter
    result = normalize_context_key("unknownParam")
    assert result is None, f"Unknown param should return None, got {result}"
    print("[OK] Unknown parameters return None")
    print()


def test_value_patterns():
    """Test value patterns for dependency resolver."""
    print("=" * 60)
    print("TEST: Value Patterns")
    print("=" * 60)

    patterns = PatternRegistry.get_value_patterns()
    assert len(patterns) >= 4, f"Expected at least 4 patterns, got {len(patterns)}"
    print(f"[OK] {len(patterns)} value patterns available")

    # Check structure
    for p in patterns:
        assert hasattr(p, 'pattern'), f"Pattern missing 'pattern' attribute"
        assert hasattr(p, 'param_type'), f"Pattern missing 'param_type' attribute"
        assert hasattr(p, 'filter_field'), f"Pattern missing 'filter_field' attribute"
        assert hasattr(p, 'description'), f"Pattern missing 'description' attribute"
    print("[OK] All patterns have required attributes")
    print()


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("PATTERN REGISTRY TESTS")
    print("=" * 60)
    print()

    test_uuid_detection()
    test_plate_detection()
    test_context_key_normalization()
    test_value_patterns()

    print("=" * 60)
    print("[SUCCESS] All pattern tests passed!")
    print("=" * 60)
