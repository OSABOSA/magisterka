#!/usr/bin/env bash
set -euo pipefail
# =============================================================================
# run-all.sh — Full pipeline: deploy k3s infrastructure then run all 8 test scenarios
#
# Usage:
#   chmod +x scripts/run-all.sh
#   ./scripts/run-all.sh
#
# Steps:
#   1. ./scripts/deploy-k3s.sh       — Full deploy (build images, apply k8s manifests)
#   2. ./scripts/run-tests.sh all    — Run all 8 k6 load-test scenarios
# =============================================================================

echo "================================================================================"
echo "  Magisterka — Full Pipeline (deploy + tests)"
echo "================================================================================"
echo ""

echo ">>> Step 1/2: Deploying infrastructure via deploy-k3s.sh ..."
./scripts/deploy-k3s.sh
echo ""

echo ">>> Step 2/2: Running all test scenarios via run-tests.sh all ..."
./scripts/run-tests.sh all
echo ""

echo "================================================================================"
echo "  Pipeline complete."
echo "================================================================================"
