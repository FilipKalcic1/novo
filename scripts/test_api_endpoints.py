#!/usr/bin/env python3
"""Test which endpoints work and which return 403."""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

async def test_endpoints():
    from services.api_gateway import APIGateway
    from config import get_settings

    settings = get_settings()
    gateway = APIGateway()  # Creates its own TokenManager

    # Test endpoints - ones that might work vs might fail
    test_cases = [
        ("get_LatestMileageReports", "GET", "/automation/MileageReports/Latest", {}),
        ("get_MileageReports", "GET", "/automation/MileageReports", {}),
        ("get_MasterData", "GET", "/automation/MasterData", {}),
        ("get_Vehicles", "GET", "/automation/Vehicles", {}),
        ("get_VehicleCalendar", "GET", "/automation/VehicleCalendar", {}),
        ("get_Expenses", "GET", "/automation/Expenses", {}),
        ("get_Cases", "GET", "/automation/Cases", {}),
        ("get_AvailableVehicles", "GET", "/automation/AvailableVehicles", {}),
    ]

    print("=" * 70)
    print("  ENDPOINT TEST - Which endpoints return 403?")
    print("=" * 70)

    # First, get and show token info
    token = await gateway.token_manager.get_token()
    print(f"\nToken (first 50 chars): {token[:50]}...")
    print(f"Scope from .env: {settings.MOBILITY_SCOPE}")
    print(f"Tenant ID: {settings.MOBILITY_TENANT_ID}")

    print("\n" + "-" * 70)

    for name, method, path, params in test_cases:
        try:
            response = await gateway.execute(
                method=method,
                path=path,
                params=params
            )

            if response.success:
                data_len = len(response.data) if isinstance(response.data, list) else 1
                print(f"OK  {name}: {response.status_code} - {data_len} items")
            else:
                print(f"ERR {name}: {response.status_code} - {response.error_code}")
                print(f"    {response.error_message[:120]}...")

        except Exception as e:
            print(f"EXC {name}: {type(e).__name__} - {e}")

        print()

    await gateway.close()

if __name__ == "__main__":
    asyncio.run(test_endpoints())
