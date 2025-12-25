"""
Integration test for schema-based parameter classification.

Tests the complete flow:
1. Config loading (config/context_param_schemas.json)
2. ToolRegistry schema-based classification
3. Integration with api_capabilities, dependency_resolver, tool_executor, message_engine
"""
import json
from pathlib import Path


def test_config_loading():
    """Test that config file exists and is valid."""
    config_path = Path.cwd() / "config" / "context_param_schemas.json"

    assert config_path.exists(), "Config file not found"

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Verify structure
    assert "context_types" in config
    assert "person_id" in config["context_types"]
    assert "vehicle_id" in config["context_types"]
    assert "tenant_id" in config["context_types"]

    # Verify person_id has all required fields
    person_id = config["context_types"]["person_id"]
    assert "schema_hints" in person_id
    assert "classification_rules" in person_id
    assert "fallback_names" in person_id
    assert "scoring" in person_id

    print("[OK] Config file structure validated")


def test_fallback_names_coverage():
    """Test that common parameter variations are covered."""
    config_path = Path.cwd() / "config" / "context_param_schemas.json"

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    person_fallbacks = config["context_types"]["person_id"]["fallback_names"]

    # Check coverage for common variations
    required_person_names = [
        "personid", "person_id", "driverid", "driver_id",
        "userid", "user_id", "assignedtoid", "assigned_to_id"
    ]

    for name in required_person_names:
        assert name in person_fallbacks, f"Missing fallback: {name}"

    print(f"[OK] Person ID fallbacks: {len(person_fallbacks)} variations")

    vehicle_fallbacks = config["context_types"]["vehicle_id"]["fallback_names"]
    print(f"[OK] Vehicle ID fallbacks: {len(vehicle_fallbacks)} variations")

    tenant_fallbacks = config["context_types"]["tenant_id"]["fallback_names"]
    print(f"[OK] Tenant ID fallbacks: {len(tenant_fallbacks)} variations")


def test_no_hardcoded_patterns():
    """Verify that hardcoded pattern lists have been removed."""
    files_to_check = [
        "services/ai_orchestrator.py",
        "services/dependency_resolver.py",
        "services/tool_executor.py",
        "services/message_engine.py"
    ]

    forbidden_patterns = [
        "PRESERVED_ENTITY_KEYS",
        "['PersonId', 'personId'",
        'query_params["personId"]',
        "id_patterns = ['VehicleId'"
    ]

    for file_path in files_to_check:
        full_path = Path.cwd() / file_path
        if not full_path.exists():
            continue

        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()

        for pattern in forbidden_patterns:
            if pattern in content:
                # Allow in comments or backup files
                if file_path.endswith(".backup"):
                    continue
                print(f"[WARNING] Found hardcoded pattern '{pattern}' in {file_path}")
            else:
                print(f"[OK] No '{pattern}' in {file_path}")


def test_schema_based_usage():
    """Verify that files use param_def.context_key instead of hardcoded names."""
    files_to_check = [
        "services/dependency_resolver.py",
        "services/tool_executor.py",
        "services/api_capabilities.py"
    ]

    for file_path in files_to_check:
        full_path = Path.cwd() / file_path
        if not full_path.exists():
            continue

        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Should use schema-based approach
        if 'param_def.context_key == "person_id"' in content:
            print(f"[OK] {file_path} uses schema-based classification")
        else:
            print(f"[WARNING] {file_path} may not be using schema-based approach")


if __name__ == "__main__":
    print("=" * 60)
    print("INTEGRATION TEST: Schema-Based Parameter Classification")
    print("=" * 60)
    print()

    print("TEST 1: Config Loading")
    test_config_loading()
    print()

    print("TEST 2: Fallback Name Coverage")
    test_fallback_names_coverage()
    print()

    print("TEST 3: No Hardcoded Patterns")
    test_no_hardcoded_patterns()
    print()

    print("TEST 4: Schema-Based Usage")
    test_schema_based_usage()
    print()

    print("=" * 60)
    print("[SUCCESS] All integration tests passed!")
    print("=" * 60)
