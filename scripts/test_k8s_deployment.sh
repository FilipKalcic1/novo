#!/bin/bash
# =============================================================================
# Kubernetes Deployment Test Script
# =============================================================================
#
# Usage:
#   ./scripts/test_k8s_deployment.sh [namespace] [service-url]
#
# Examples:
#   ./scripts/test_k8s_deployment.sh                    # Default: novo-bot namespace
#   ./scripts/test_k8s_deployment.sh novo-bot           # Specify namespace
#   ./scripts/test_k8s_deployment.sh novo-bot http://localhost:8000  # Local testing
#
# =============================================================================

set -e

NAMESPACE="${1:-novo-bot}"
SERVICE_URL="${2:-}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=========================================="
echo "Kubernetes Deployment Test"
echo "Namespace: $NAMESPACE"
echo "=========================================="
echo ""

# Track test results
PASSED=0
FAILED=0

pass() {
    echo -e "${GREEN}✓ $1${NC}"
    ((PASSED++))
}

fail() {
    echo -e "${RED}✗ $1${NC}"
    ((FAILED++))
}

warn() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

# =============================================================================
# 1. Check Kubernetes Resources
# =============================================================================
echo "--- Checking Kubernetes Resources ---"

# Check namespace
if kubectl get namespace "$NAMESPACE" &>/dev/null; then
    pass "Namespace '$NAMESPACE' exists"
else
    fail "Namespace '$NAMESPACE' not found"
fi

# Check deployments
for deployment in novo-api novo-worker; do
    if kubectl get deployment "$deployment" -n "$NAMESPACE" &>/dev/null; then
        READY=$(kubectl get deployment "$deployment" -n "$NAMESPACE" -o jsonpath='{.status.readyReplicas}')
        DESIRED=$(kubectl get deployment "$deployment" -n "$NAMESPACE" -o jsonpath='{.spec.replicas}')
        if [ "$READY" == "$DESIRED" ]; then
            pass "Deployment '$deployment': $READY/$DESIRED replicas ready"
        else
            fail "Deployment '$deployment': $READY/$DESIRED replicas ready (not all ready)"
        fi
    else
        fail "Deployment '$deployment' not found"
    fi
done

# Check services
if kubectl get service novo-api-service -n "$NAMESPACE" &>/dev/null; then
    pass "Service 'novo-api-service' exists"
else
    fail "Service 'novo-api-service' not found"
fi

# Check secrets
if kubectl get secret novo-secrets -n "$NAMESPACE" &>/dev/null; then
    pass "Secret 'novo-secrets' exists"
else
    fail "Secret 'novo-secrets' not found"
fi

# Check configmap
if kubectl get configmap novo-config -n "$NAMESPACE" &>/dev/null; then
    pass "ConfigMap 'novo-config' exists"
else
    fail "ConfigMap 'novo-config' not found"
fi

# Check HPA
for hpa in novo-api-hpa novo-worker-hpa; do
    if kubectl get hpa "$hpa" -n "$NAMESPACE" &>/dev/null; then
        pass "HPA '$hpa' exists"
    else
        warn "HPA '$hpa' not found (optional)"
    fi
done

echo ""

# =============================================================================
# 2. Check Pod Health
# =============================================================================
echo "--- Checking Pod Health ---"

# Get API pods
API_PODS=$(kubectl get pods -n "$NAMESPACE" -l app=novo-bot,component=api -o jsonpath='{.items[*].metadata.name}')

for pod in $API_PODS; do
    STATUS=$(kubectl get pod "$pod" -n "$NAMESPACE" -o jsonpath='{.status.phase}')
    if [ "$STATUS" == "Running" ]; then
        pass "Pod '$pod' is Running"

        # Check container readiness
        READY=$(kubectl get pod "$pod" -n "$NAMESPACE" -o jsonpath='{.status.containerStatuses[0].ready}')
        if [ "$READY" == "true" ]; then
            pass "Pod '$pod' container is Ready"
        else
            fail "Pod '$pod' container is not Ready"
        fi
    else
        fail "Pod '$pod' is $STATUS (expected Running)"
    fi
done

echo ""

# =============================================================================
# 3. Test Endpoints (if SERVICE_URL provided or port-forward)
# =============================================================================
echo "--- Testing Endpoints ---"

if [ -z "$SERVICE_URL" ]; then
    # Try to port-forward
    echo "No SERVICE_URL provided, attempting port-forward..."

    # Kill any existing port-forward
    pkill -f "kubectl port-forward.*8000:8000" 2>/dev/null || true

    # Start port-forward in background
    kubectl port-forward service/novo-api-service 8000:80 -n "$NAMESPACE" &>/dev/null &
    PF_PID=$!
    sleep 3

    SERVICE_URL="http://localhost:8000"
    CLEANUP_PF=true
else
    CLEANUP_PF=false
fi

# Test liveness endpoint
echo "Testing $SERVICE_URL/health/live ..."
if curl -sf "$SERVICE_URL/health/live" &>/dev/null; then
    RESPONSE=$(curl -s "$SERVICE_URL/health/live")
    pass "Liveness endpoint: $RESPONSE"
else
    fail "Liveness endpoint failed"
fi

# Test readiness endpoint
echo "Testing $SERVICE_URL/health/ready ..."
if curl -sf "$SERVICE_URL/health/ready" &>/dev/null; then
    RESPONSE=$(curl -s "$SERVICE_URL/health/ready")
    STATUS=$(echo "$RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('status', 'unknown'))" 2>/dev/null || echo "parse-error")
    if [ "$STATUS" == "ready" ]; then
        pass "Readiness endpoint: status=$STATUS"
    else
        fail "Readiness endpoint: status=$STATUS (expected 'ready')"
    fi
else
    fail "Readiness endpoint failed"
fi

# Test metrics endpoint
echo "Testing $SERVICE_URL/metrics ..."
if curl -sf "$SERVICE_URL/metrics" &>/dev/null; then
    METRICS_COUNT=$(curl -s "$SERVICE_URL/metrics" | grep -c "^novo_" || echo "0")
    if [ "$METRICS_COUNT" -gt 0 ]; then
        pass "Metrics endpoint: $METRICS_COUNT novo_* metrics found"
    else
        warn "Metrics endpoint works but no novo_* metrics found"
    fi
else
    fail "Metrics endpoint failed"
fi

# Cleanup port-forward if we started it
if [ "$CLEANUP_PF" == "true" ]; then
    kill $PF_PID 2>/dev/null || true
fi

echo ""

# =============================================================================
# 4. Summary
# =============================================================================
echo "=========================================="
echo "Test Summary"
echo "=========================================="
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"
echo ""

if [ $FAILED -gt 0 ]; then
    echo -e "${RED}Some tests failed!${NC}"
    exit 1
else
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
fi
