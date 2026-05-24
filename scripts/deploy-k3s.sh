#!/usr/bin/env bash
# =============================================================================
# deploy-k3s.sh — Faza IV: Deployment mikroserwisów na k3s (QNAP TS-464)
#
# Uruchom z poziomu QNAP-a (gdzie jest Docker i kubectl):
#   chmod +x scripts/deploy-k3s.sh
#   ./scripts/deploy-k3s.sh
#
# Działanie:
#   1. Buduje obrazy Docker (cpu-service, io-service, memory-service)
#   2. Importuje obrazy do containerd k3s
#   3. Nakłada manifesty Kubernetes w poprawnej kolejności
#   4. Czeka na gotowość deploymentów
#   5. Wyświetla podsumowanie
# =============================================================================

set -euo pipefail

# ---- Konfiguracja -----------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
K8S_DIR="${PROJECT_DIR}/k8s"
APPS_DIR="${PROJECT_DIR}/apps"
NAMESPACE="magisterka"

# Kolory
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ---- Sprawdzenie wymagań ---------------------------------------------------
check_prereqs() {
    info "Sprawdzanie wymagań (Docker, kubectl, k3s)..."

    if ! command -v docker &>/dev/null; then
        err "Docker nie jest dostępny — wymagany do budowania obrazów"
        return 1
    fi
    ok "Docker: $(docker --version)"

    if ! command -v kubectl &>/dev/null; then
        err "kubectl nie jest dostępny — zainstaluj lub użyj 'k3s kubectl'"
        return 1
    fi
    ok "kubectl: $(kubectl version --client --short 2>/dev/null || kubectl version --client)"

    # Sprawdź czy k3s działa
    if ! kubectl cluster-info &>/dev/null; then
        err "Nie można połączyć z klastrem Kubernetes. Czy k3s działa?"
        warn "Spróbuj: sudo systemctl status k3s"
        return 1
    fi
    ok "Klaster k3s odpowiada"
}

# ---- Budowanie obrazów Docker -----------------------------------------------
build_images() {
    info "Budowanie obrazów Docker..."

    local services=("cpu-service" "io-service")
    for svc in "${services[@]}"; do
        info "Budowanie ${svc}..."
        docker build -t "${svc}:latest" "${APPS_DIR}/${svc}/"
        ok "${svc}:latest zbudowany"
    done
}

# ---- Import obrazów do containerd k3s ---------------------------------------
import_to_k3s() {
    info "Importowanie obrazów do containerd k3s..."

    local services=("cpu-service" "io-service")
    for svc in "${services[@]}"; do
        info "Importowanie ${svc}:latest → containerd k3s..."
        
        # Metoda 1: docker save + k3s ctr image import (działa na większości k3s)
        if command -v k3s &>/dev/null && k3s ctr image --help &>/dev/null 2>&1; then
            docker save "${svc}:latest" | k3s ctr image import -
            ok "${svc} zaimportowany (k3s ctr)"
        # Metoda 2: docker save + ctr image import (bezpośrednio containerd k3s)
        elif [ -S /run/k3s/containerd/containerd.sock ]; then
            docker save "${svc}:latest" | \
                sudo ctr --namespace k8s.io --address /run/k3s/containerd/containerd.sock \
                image import -
            ok "${svc} zaimportowany (containerd socket)"
        # Metoda 3: crictl (jeśli dostępne)
        elif command -v crictl &>/dev/null; then
            docker save "${svc}:latest" | \
                sudo ctr --namespace k8s.io image import -
            ok "${svc} zaimportowany (ctr)"
        else
            err "Nie można zaimportować ${svc} — brak k3s, ctr lub crictl"
            err "Spróbuj ręcznie: docker save ${svc}:latest | sudo k3s ctr image import -"
            return 1
        fi
    done
}

# ---- Nakładanie manifestów Kubernetes ---------------------------------------
apply_manifests() {
    info "Nakładanie manifestów Kubernetes..."

    # 1. Namespace
    info "Tworzenie namespace: ${NAMESPACE}"
    kubectl apply -f "${K8S_DIR}/base/namespace.yaml"

    # 2. RBAC dla Prometheusa (przed monitoringiem)
    kubectl apply -f "${K8S_DIR}/monitoring/prometheus-config.yaml"
    kubectl apply -f "${K8S_DIR}/monitoring/prometheus.yaml"
    kubectl apply -f "${K8S_DIR}/monitoring/grafana.yaml"
    kubectl apply -f "${K8S_DIR}/monitoring/prometheus-adapter-config.yaml"
    kubectl apply -f "${K8S_DIR}/monitoring/prometheus-adapter.yaml"

    # 3. Serwisy aplikacyjne (kolejność: deployment → service → hpa-cpu)
    local apps=("cpu-service" "io-service")
    for app in "${apps[@]}"; do
        info "Deployowanie ${app}..."
        kubectl apply -f "${K8S_DIR}/${app}/deployment.yaml"
        kubectl apply -f "${K8S_DIR}/${app}/service.yaml"
        kubectl apply -f "${K8S_DIR}/${app}/hpa-cpu.yaml"
    done

    # 4. Ingress (na końcu, po serwisach)
    kubectl apply -f "${K8S_DIR}/ingress.yaml"

    ok "Wszystkie manifesty nałożone"
}

# ---- Czekanie na gotowość ---------------------------------------------------
wait_for_ready() {
    info "Czekanie na gotowość deploymentów (max 120s)..."

    local deployments=("cpu-service" "io-service" "prometheus" "grafana" "prometheus-adapter")
    for dep in "${deployments[@]}"; do
        info "Czekanie na ${dep}..."
        if kubectl -n "${NAMESPACE}" rollout status "deployment/${dep}" --timeout=120s 2>/dev/null; then
            ok "${dep} gotowy"
        else
            warn "${dep} nie osiągnął gotowości w 120s — sprawdź: kubectl -n ${NAMESPACE} describe pod -l app=${dep}"
        fi
    done
}

# ---- Podsumowanie -----------------------------------------------------------
print_summary() {
    echo ""
    echo "=============================================================================="
    echo -e "${GREEN}  DEPLOYMENT ZAKOŃCZONY${NC}"
    echo "=============================================================================="
    echo ""
    echo "  Namespace: ${NAMESPACE}"
    echo ""
    echo "  Pody:"
    kubectl -n "${NAMESPACE}" get pods -o wide 2>/dev/null || true
    echo ""
    echo "  Serwisy:"
    kubectl -n "${NAMESPACE}" get svc 2>/dev/null || true
    echo ""
    echo "  Ingress:"
    kubectl -n "${NAMESPACE}" get ingress 2>/dev/null || true
    echo ""
    echo "  HPA:"
    kubectl -n "${NAMESPACE}" get hpa 2>/dev/null || true
    echo ""
    echo "----------------------------------------------------------------------------"
    echo "  Testowanie lokalne (port-forward):"
    echo "    kubectl -n ${NAMESPACE} port-forward svc/cpu-service 8080:80"
    echo "    curl http://localhost:8080/health"
    echo ""
    echo "  Testowanie przez Ingress (dodaj do /etc/hosts na maszynie klienckiej):"
    local node_ip=$(kubectl get node -o jsonpath='{.items[0].status.addresses[0].address}' 2>/dev/null || echo "QNAP_IP")
    echo "    ${node_ip} cpu-magisterka.k3s.local io-magisterka.k3s.local memory-magisterka.k3s.local"
    echo "----------------------------------------------------------------------------"
}

# ---- Czyszczenie (opcjonalne) -----------------------------------------------
cleanup() {
    warn "CZYSZCZENIE: Usuwanie wszystkich zasobów magisterka z k3s..."
    kubectl delete namespace "${NAMESPACE}" --ignore-not-found=true
    ok "Namespace ${NAMESPACE} usunięty"
}

# ---- Main -------------------------------------------------------------------
main() {
    echo ""
    echo "=============================================================================="
    echo "  Magisterka — Deployment na k3s"
    echo "=============================================================================="
    echo ""

    case "${1:-deploy}" in
        deploy|"")
            check_prereqs
            build_images
            import_to_k3s
            apply_manifests
            wait_for_ready
            print_summary
            ;;
        build-only)
            build_images
            import_to_k3s
            ;;
        apply-only)
            apply_manifests
            wait_for_ready
            print_summary
            ;;
        clean|cleanup)
            cleanup
            ;;
        status)
            print_summary
            ;;
        *)
            echo "Użycie: $0 [deploy|build-only|apply-only|clean|status]"
            echo "  deploy      — pełny cykl: build → import → deploy → wait (domyślnie)"
            echo "  build-only  — tylko budowanie obrazów i import do k3s"
            echo "  apply-only  — tylko nałożenie manifestów (zakłada gotowe obrazy)"
            echo "  clean       — usuwa namespace magisterka (wszystkie zasoby)"
            echo "  status      — pokazuje status deploymentu"
            exit 1
            ;;
    esac
}

main "$@"
