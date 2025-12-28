#!/usr/bin/env python3
"""
Local Endpoint Test Script

Tests health, readiness, and metrics endpoints locally.
Can be used with docker-compose or direct uvicorn.

Usage:
    python scripts/test_endpoints.py [base_url]

Examples:
    python scripts/test_endpoints.py                    # Default: http://localhost:8000
    python scripts/test_endpoints.py http://localhost:8080
"""

import sys
import json
import time
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError


def test_endpoint(url: str, name: str, expected_status: int = 200) -> bool:
    """Test an endpoint and return True if successful."""
    try:
        req = Request(url, headers={"Accept": "application/json"})
        response = urlopen(req, timeout=10)

        if response.status != expected_status:
            print(f"  FAIL: {name} - Status {response.status} (expected {expected_status})")
            return False

        body = response.read().decode()
        try:
            data = json.loads(body)
            print(f"  OK: {name} - {json.dumps(data)[:100]}")
        except json.JSONDecodeError:
            # Metrics endpoint returns plain text
            lines = body.strip().split('\n')
            novo_metrics = [l for l in lines if l.startswith('novo_') and not l.startswith('#')]
            print(f"  OK: {name} - {len(novo_metrics)} novo_* metrics")

        return True

    except HTTPError as e:
        print(f"  FAIL: {name} - HTTP {e.code}: {e.reason}")
        return False
    except URLError as e:
        print(f"  FAIL: {name} - Connection error: {e.reason}")
        return False
    except Exception as e:
        print(f"  FAIL: {name} - Error: {e}")
        return False


def main():
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"

    print(f"\n{'='*60}")
    print(f"Testing endpoints at: {base_url}")
    print(f"{'='*60}\n")

    # Wait a moment for service to be ready
    print("Waiting for service...")
    for i in range(5):
        try:
            urlopen(f"{base_url}/health/live", timeout=2)
            break
        except Exception:
            time.sleep(1)
    print()

    tests = [
        (f"{base_url}/", "Root endpoint"),
        (f"{base_url}/health/live", "Liveness probe"),
        (f"{base_url}/health/ready", "Readiness probe"),
        (f"{base_url}/health", "Health (legacy)"),
        (f"{base_url}/metrics", "Prometheus metrics"),
    ]

    passed = 0
    failed = 0

    for url, name in tests:
        if test_endpoint(url, name):
            passed += 1
        else:
            failed += 1

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'='*60}\n")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
