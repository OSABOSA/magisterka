#!/usr/bin/env bash
# =============================================================================
# run-tests.sh — Faza V: Badania eksperymentalne (macierz 2×3 k6 load tests)
#
# Uruchom na QNAP-ie (gdzie jest kubectl i k6):
#   chmod +x scripts/run-tests.sh
#   ./scripts/run-tests.sh
#
# Działanie:
#   1. Sprawdza wymagania (kubectl, k6)
#   2. Uruchamia port-forward dla każdego serwisu
#   3. Dla każdego scenariusza: przełącza HPA → resetuje deployment → odpala k6
#   4. Zapisuje wyniki do results/<scenario>/
#   5. Sprząta port-forward
#   6. Wyświetla podsumowanie
#
# Scenariusze (macierz 2×3):
#   baseline-cpu     – CPU-bound, brak HPA, 1 replika
#   baseline-io      – I/O-bound, brak HPA, 1 replika
#   cpu-cpu          – CPU-bound + HPA CPU
#   cpu-memory       – CPU-bound + HPA Memory
#   cpu-custom       – CPU-bound + HPA Custom (RPS)
#   io-cpu           – I/O-bound + HPA CPU
#   io-memory        – I/O-bound + HPA Memory
#   io-custom        – I/O-bound + HPA Custom (RPS)
#
# Użycie:
#   ./scripts/run-tests.sh                    # wszystkie 8 scenariuszy
#   ./scripts/run-tests.sh cpu-cpu            # pojedynczy scenariusz
#   ./scripts/run-tests.sh quick              # szybki smoke test
#   ./scripts/run-tests.sh stop               # zatrzymaj port-forwardy
# =============================================================================

set -euo pipefail

# ---- Konfiguracja -----------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
TESTS_DIR="${PROJECT_DIR}/tests/k6"
RESULTS_DIR="${PROJECT_DIR}/results"
K8S_DIR="${PROJECT_DIR}/k8s"
NAMESPACE="magisterka"
REPETITIONS=5

# Porty dla port-forward
CPU_PORT=8080
IO_PORT=8081

# Kolory
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ---- Sprawdzanie wymagań ----------------------------------------------------
check_prereqs() {
    info "Sprawdzanie wymagań..."

    if command -v kubectl &>/dev/null; then
        KUBECTL="kubectl"
    elif command -v k3s &>/dev/null; then
        KUBECTL="k3s kubectl"
    else
        err "kubectl nie jest dostępny"
        return 1
    fi
    ok "kubectl dostępny"

    if ! command -v k6 &>/dev/null; then
        err "k6 nie jest dostępny — instaluj: https://k6.io/docs/get-started/installation/"
        return 1
    fi
    ok "k6: $(k6 version 2>&1 | head -1)"

    local pods_ok
    pods_ok=$(${KUBECTL} -n "${NAMESPACE}" get pods -o jsonpath='{.items[?(@.status.phase=="Running")].metadata.name}' 2>/dev/null | wc -w)
    if [ "${pods_ok}" -lt 2 ]; then
        warn "Tylko ${pods_ok} pod(ów) Running — testy mogą nie działać"
    else
        ok "${pods_ok} podów Running"
    fi
}

# ---- Port-forward -----------------------------------------------------------
start_port_forwards() {
    info "Uruchamianie port-forward dla serwisów..."

    pkill -f "port-forward.*${NAMESPACE}" 2>/dev/null || true
    sleep 1

    # CPU-service
    nohup ${KUBECTL} -n "${NAMESPACE}" port-forward "svc/cpu-service" "${CPU_PORT}:80" &>/tmp/pf-cpu.log &
    echo $! > /tmp/pf-cpu.pid
    ok "CPU-service → localhost:${CPU_PORT} (pid $(cat /tmp/pf-cpu.pid))"

    # IO-service
    nohup ${KUBECTL} -n "${NAMESPACE}" port-forward "svc/io-service" "${IO_PORT}:80" &>/tmp/pf-io.log &
    echo $! > /tmp/pf-io.pid
    ok "IO-service → localhost:${IO_PORT} (pid $(cat /tmp/pf-io.pid))"

    sleep 3

    local all_up=true
    for svc in "cpu:${CPU_PORT}" "io:${IO_PORT}"; do
        local name="${svc%%:*}"
        local port="${svc##*:}"
        if curl -sf "http://localhost:${port}/health" &>/dev/null; then
            ok "${name}-service health OK"
        else
            err "${name}-service health FAIL — sprawdź /tmp/pf-${name}.log"
            all_up=false
        fi
    done

    if ! $all_up; then
        err "Nie wszystkie port-forwardy działają — przerywam"
        stop_port_forwards
        return 1
    fi
}

stop_port_forwards() {
    info "Zatrzymywanie port-forward..."
    for pidfile in /tmp/pf-cpu.pid /tmp/pf-io.pid; do
        if [ -f "${pidfile}" ]; then
            kill "$(cat "${pidfile}")" 2>/dev/null || true
            rm -f "${pidfile}"
        fi
    done
    pkill -f "port-forward.*${NAMESPACE}" 2>/dev/null || true
    ok "Port-forward zatrzymany"
}

# ---- Przełączanie HPA -------------------------------------------------------
# switch_hpa <service> <strategy>
#   service:  cpu-service | io-service
#   strategy: cpu | memory | custom | none
#
# Usuwa wszystkie istniejące HPA dla danego serwisu,
# następnie nakłada odpowiedni plik (chyba że strategy=none).
switch_hpa() {
    local service="$1"
    local strategy="$2"

    info "Przełączanie HPA dla ${service} → strategia: ${strategy}"

    # Usuń wszystkie istniejące HPA dla tego serwisu
    local hpa_names
    hpa_names=$(${KUBECTL} -n "${NAMESPACE}" get hpa -o name 2>/dev/null | grep "${service}" || true)
    if [ -n "${hpa_names}" ]; then
        for hpa in ${hpa_names}; do
            ${KUBECTL} -n "${NAMESPACE}" delete "${hpa}" --ignore-not-found=true
        done
        ok "  Usunięto poprzednie HPA dla ${service}"
    fi

    # Nakładanie wybranej strategii
    case "${strategy}" in
        cpu)
            ${KUBECTL} apply -f "${K8S_DIR}/${service}/hpa-cpu.yaml"
            ok "  Nałożono HPA CPU"
            ;;
        memory)
            ${KUBECTL} apply -f "${K8S_DIR}/${service}/hpa-memory.yaml"
            ok "  Nałożono HPA Memory"
            ;;
        custom)
            ${KUBECTL} apply -f "${K8S_DIR}/${service}/hpa-custom.yaml"
            ok "  Nałożono HPA Custom (RPS)"
            ;;
        none)
            ok "  Brak HPA (baseline)"
            ;;
        *)
            err "  Nieznana strategia: ${strategy}"
            return 1
            ;;
    esac

    sleep 2
}

# ---- Reset deploymentu ------------------------------------------------------
# reset_deployment <service> [replicas]
# Skaluje deployment do zadanego replicas (domyślnie 1) i czeka na gotowość.
reset_deployment() {
    local service="$1"
    local replicas="${2:-1}"

    info "Resetowanie ${service} → ${replicas} replika(i)..."

    ${KUBECTL} -n "${NAMESPACE}" scale deployment "${service}" --replicas="${replicas}"
    ${KUBECTL} -n "${NAMESPACE}" rollout status "deployment/${service}" --timeout=120s 2>/dev/null || true

    sleep 5
    ok "  ${service} gotowy (${replicas} replika(i))"
}

# ---- Cooldown między scenariuszami (D7) -------------------------------------
cooldown() {
    info "===== COOLDOWN: Stabilizacja klastra (120s) ====="

    # 1. Skaluj oba serwisy do 1 repliki
    info "Skalowanie cpu-service i io-service → 1 replika..."
    ${KUBECTL} -n "${NAMESPACE}" scale deployment cpu-service io-service --replicas=1 2>/dev/null || true

    # 2. Usuń wszystkie aktywne HPA
    info "Usuwanie wszystkich HPA w namespace ${NAMESPACE}..."
    ${KUBECTL} -n "${NAMESPACE}" delete hpa --all --ignore-not-found=true 2>/dev/null || true

    # 3. Czekaj 120s na stabilizację
    info "Oczekiwanie 120s na stabilizację klastra..."
    sleep 120

    ok "Cooldown zakończony — klaster ustabilizowany"
    echo ""
}

# ---- Uruchamianie pojedynczego scenariusza ----------------------------------
run_scenario() {
    local scenario="$1"          # np. "cpu-cpu"
    local service="$2"           # np. "cpu-service"
    local hpa_strategy="$3"      # np. "cpu" | "none"
    local k6_script="$4"         # ścieżka do skryptu k6
    local base_url="$5"          # URL serwisu (localhost:PORT)
    local description="$6"       # opis do logów

    echo ""
    echo "============================================================================"
    info "SCENARIUSZ: ${scenario}"
    info "Opis:     ${description}"
    info "Serwis:   ${service}"
    info "HPA:      ${hpa_strategy}"
    info "Powtórzeń: ${REPETITIONS}"
    info "Start:    $(date)"
    echo "============================================================================"

    # 1. Przełącz HPA
    switch_hpa "${service}" "${hpa_strategy}"

    # 2. Resetuj deployment
    reset_deployment "${service}" 1

    # 3. Uruchom test k6 REPETITIONS razy (D5)
    local final_exit_code=0
    for i in $(seq 1 ${REPETITIONS}); do
        local timestamp
        timestamp=$(date +%Y%m%d_%H%M%S)
        local run_dir="${RESULTS_DIR}/${scenario}/run-${i}"
        mkdir -p "${run_dir}"

        local json_output="${run_dir}/${scenario}_run${i}_${timestamp}.json"
        local txt_output="${run_dir}/${scenario}_run${i}_${timestamp}.txt"

        info "  Powtórzenie ${i}/${REPETITIONS}: k6 ${k6_script} → ${base_url}"
        k6 run \
            --out json="${json_output}" \
            --summary-export="${json_output}" \
            -e "BASE_URL=${base_url}" \
            "${k6_script}" 2>&1 | tee "${txt_output}"

        local exit_code=${PIPESTATUS[0]}

        # Zapisz metadane pojedynczego uruchomienia
        cat > "${run_dir}/metadata.json" << METADATA_EOF
{
  "scenario": "${scenario}",
  "service": "${service}",
  "hpa_strategy": "${hpa_strategy}",
  "description": "${description}",
  "k6_script": "${k6_script}",
  "repetition": ${i},
  "total_repetitions": ${REPETITIONS},
  "timestamp": "${timestamp}",
  "exit_code": ${exit_code}
}
METADATA_EOF

        if [ ${exit_code} -eq 0 ]; then
            ok "  Powtórzenie ${i}/${REPETITIONS} zakończone pomyślnie"
        else
            warn "  Powtórzenie ${i}/${REPETITIONS} zakończone z kodem ${exit_code}"
            final_exit_code=${exit_code}
        fi
    done

    ok "Scenariusz ${scenario} zakończony (${REPETITIONS} powtórzeń)"
    echo ""
    return ${final_exit_code}
}

# ---- Scenariusze — macierz 2×3 + baseline -----------------------------------

scenario_baseline_cpu() {
    run_scenario \
        "baseline-cpu" \
        "cpu-service" \
        "none" \
        "${TESTS_DIR}/cpu-load-test.js" \
        "http://localhost:${CPU_PORT}" \
        "CPU-bound, brak HPA, 1 replika (baseline)"
}

scenario_baseline_io() {
    run_scenario \
        "baseline-io" \
        "io-service" \
        "none" \
        "${TESTS_DIR}/io-load-test.js" \
        "http://localhost:${IO_PORT}" \
        "I/O-bound, brak HPA, 1 replika (baseline)"
}

scenario_cpu_cpu() {
    run_scenario \
        "cpu-cpu" \
        "cpu-service" \
        "cpu" \
        "${TESTS_DIR}/cpu-load-test.js" \
        "http://localhost:${CPU_PORT}" \
        "CPU-bound + HPA CPU (target 50%)"
}

scenario_cpu_memory() {
    run_scenario \
        "cpu-memory" \
        "cpu-service" \
        "memory" \
        "${TESTS_DIR}/cpu-load-test.js" \
        "http://localhost:${CPU_PORT}" \
        "CPU-bound + HPA Memory (target 70%)"
}

scenario_cpu_custom() {
    run_scenario \
        "cpu-custom" \
        "cpu-service" \
        "custom" \
        "${TESTS_DIR}/cpu-load-test.js" \
        "http://localhost:${CPU_PORT}" \
        "CPU-bound + HPA Custom RPS"
}

scenario_io_cpu() {
    run_scenario \
        "io-cpu" \
        "io-service" \
        "cpu" \
        "${TESTS_DIR}/io-load-test.js" \
        "http://localhost:${IO_PORT}" \
        "I/O-bound + HPA CPU (target 50%)"
}

scenario_io_memory() {
    run_scenario \
        "io-memory" \
        "io-service" \
        "memory" \
        "${TESTS_DIR}/io-load-test.js" \
        "http://localhost:${IO_PORT}" \
        "I/O-bound + HPA Memory (target 70%)"
}

scenario_io_custom() {
    run_scenario \
        "io-custom" \
        "io-service" \
        "custom" \
        "${TESTS_DIR}/io-load-test.js" \
        "http://localhost:${IO_PORT}" \
        "I/O-bound + HPA Custom RPS"
}

# ---- Quick test (krótki smoke test) -----------------------------------------
test_quick() {
    info "===== QUICK SMOKE TEST (~1 min) ====="

    local quick_script="${RESULTS_DIR}/quick-test.js"
    cat > "${quick_script}" << 'QUICKEOF'
import http from 'k6/http';
import { check } from 'k6';

const CPU = __ENV.BASE_URL || 'http://localhost:8080';
const IO = __ENV.BASE_URL || 'http://localhost:8080';

export const options = {
    vus: 5,
    duration: '30s',
    thresholds: {
        'http_req_failed': ['rate<0.1'],
    },
};

export default function() {
    // CPU
    let r = http.get(`${CPU}/fibonacci?n=10`);
    check(r, { 'cpu fibonacci 200': (r) => r.status === 200 });

    // IO
    r = http.get(`${IO}/query?delay=50&steps=1&external_call=false`);
    check(r, { 'io query 200': (r) => r.status === 200 });
}
QUICKEOF

    # Quick test używa CPU_PORT jako BASE_URL (CPU-service tylko)
    local timestamp=$(date +%Y%m%d_%H%M%S)
    local quick_dir="${RESULTS_DIR}/quick"
    mkdir -p "${quick_dir}"

    k6 run \
        --out json="${quick_dir}/quick_${timestamp}.json" \
        --summary-export="${quick_dir}/quick_${timestamp}.json" \
        -e "BASE_URL=http://localhost:${CPU_PORT}" \
        -e "IO_URL=http://localhost:${IO_PORT}" \
        "${quick_script}" 2>&1 | tee "${quick_dir}/quick_${timestamp}.txt"

    rm -f "${quick_script}"
    ok "Quick test zakończony"
}

# ---- Pre-flight test (~5 min, triggers HPA scale-up) ------------------------
test_preflight() {
    info "===== PRE-FLIGHT TEST (~5 min) ====="
    echo ""
    info "Cel: Wygenerować obciążenie CPU wystarczające do triggerowania HPA scale-up"
    info "Serwis: cpu-service (HPA CPU, target 50%, minReplicas=2, maxReplicas=10)"
    info "Endpoint: /fibonacci?n=25 (czysty CPU-bound, O(2^n))"
    echo ""

    # 1. Ensure cpu-service port-forward
    info "Uruchamianie port-forward dla cpu-service..."
    pkill -f "port-forward.*cpu-service.*${NAMESPACE}" 2>/dev/null || true
    sleep 1
    nohup ${KUBECTL} -n "${NAMESPACE}" port-forward "svc/cpu-service" "${CPU_PORT}:80" &>/tmp/pf-cpu.log &
    echo $! > /tmp/pf-cpu.pid
    sleep 2
    if curl -sf "http://localhost:${CPU_PORT}/health" &>/dev/null; then
        ok "cpu-service health OK na localhost:${CPU_PORT}"
    else
        err "cpu-service health FAIL — sprawdź /tmp/pf-cpu.log"
        return 1
    fi

    # 2. Apply default HPA (CPU)
    info "Nakładanie HPA CPU na cpu-service..."
    ${KUBECTL} apply -f "${K8S_DIR}/cpu-service/hpa-cpu.yaml"
    ok "HPA CPU nałożony (minReplicas=2, maxReplicas=10, target=50% CPU)"

    # 3. Scale to minReplicas to establish baseline
    info "Skalowanie cpu-service → 2 repliki (HPA baseline)..."
    ${KUBECTL} -n "${NAMESPACE}" scale deployment cpu-service --replicas=2
    ${KUBECTL} -n "${NAMESPACE}" rollout status "deployment/cpu-service" --timeout=120s 2>/dev/null || true
    sleep 3
    ok "cpu-service: 2 repliki (HPA będzie skalować od tej bazy)"

    # 4. Start Grafana port-forward
    info "Uruchamianie Grafana port-forward..."
    pkill -f "port-forward.*grafana.*${NAMESPACE}" 2>/dev/null || true
    sleep 1
    nohup ${KUBECTL} -n "${NAMESPACE}" port-forward svc/grafana 3000:3000 &>/tmp/pf-grafana.log &
    local grafana_pid=$!
    echo "${grafana_pid}" > /tmp/pf-grafana.pid
    sleep 2

    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║  🌐 Open Grafana at http://localhost:3000                   ║${NC}"
    echo -e "${GREEN}║     to watch scaling live!                                  ║${NC}"
    echo -e "${GREEN}║                                                            ║${NC}"
    echo -e "${GREEN}║  Dashboard: CPU-Service Overview                           ║${NC}"
    echo -e "${GREEN}║  Default credentials: admin / admin                        ║${NC}"
    echo -e "${GREEN}║                                                            ║${NC}"
    echo -e "${GREEN}║  Grafana PID: ${grafana_pid}                                          ║${NC}"
    echo -e "${GREEN}║  Kill later:  kill ${grafana_pid}                                       ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    # 5. Generate k6 preflight script
    local preflight_script="${RESULTS_DIR}/preflight-test.js"
    cat > "${preflight_script}" << 'PREFLIGHTEOF'
import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';

export const options = {
    scenarios: {
        fibonacci_ramp: {
            executor: 'ramping-vus',
            startVUs: 1,
            stages: [
                { duration: '90s', target: 50 },    // ramp-up: 1→50 VUs
                { duration: '120s', target: 50 },   // steady: 50 VUs sustained
                { duration: '60s', target: 0 },     // ramp-down: 50→0 VUs
            ],
            exec: 'fibonacciBurn',
            gracefulRampDown: '30s',
        },
    },
    thresholds: {
        'http_req_duration': ['p(95)<10000'],
        'http_req_failed': ['rate<0.1'],
    },
};

export function fibonacciBurn() {
    // n=25 gives ~1-5ms per call; 50 concurrent VUs saturate CPU on 2 pods
    const url = `${BASE_URL}/fibonacci?n=25`;
    const res = http.get(url);

    check(res, {
        'fibonacci: status 200': (r) => r.status === 200,
    });

    // Minimal sleep to keep request rate high
    sleep(0.05 + Math.random() * 0.1); // 50-150ms
}
PREFLIGHTEOF

    # 6. Run the preflight k6 test
    local timestamp
    timestamp=$(date +%Y%m%d_%H%M%S)
    local preflight_dir="${RESULTS_DIR}/preflight"
    mkdir -p "${preflight_dir}"

    info "Start testu pre-flight: $(date)"
    info "Czas trwania: ~4.5 min (90s ramp-up + 120s steady + 60s ramp-down)"
    echo ""
    info "Obserwuj HPA: kubectl -n ${NAMESPACE} get hpa -w"
    echo ""

    k6 run \
        --out json="${preflight_dir}/preflight_${timestamp}.json" \
        --summary-export="${preflight_dir}/preflight_${timestamp}.json" \
        -e "BASE_URL=http://localhost:${CPU_PORT}" \
        "${preflight_script}" 2>&1 | tee "${preflight_dir}/preflight_${timestamp}.txt"

    local exit_code=${PIPESTATUS[0]}

    # 7. Check if HPA scaled
    echo ""
    info "===== PRE-FLIGHT RESULTS ====="
    info "Stan HPA po teście:"
    ${KUBECTL} -n "${NAMESPACE}" get hpa cpu-service-hpa -o wide 2>/dev/null || true
    echo ""
    info "Pody cpu-service:"
    ${KUBECTL} -n "${NAMESPACE}" get pods -l app=cpu-service -o wide 2>/dev/null || true
    echo ""

    if [ ${exit_code} -eq 0 ]; then
        ok "Pre-flight test zakończony pomyślnie (exit code 0)"
    else
        warn "Pre-flight test zakończony z kodem ${exit_code}"
    fi

    echo ""
    info "Grafana nadal działa na http://localhost:3000 (PID: ${grafana_pid})"
    info "Aby zatrzymać Grafana: kill ${grafana_pid}"
    info "Aby zatrzymać cpu-service port-forward: kill $(cat /tmp/pf-cpu.pid 2>/dev/null)"
    echo ""

    # Clean up temp script
    rm -f "${preflight_script}"
}

# ---- Zbieranie metryk Prometheus po testach ---------------------------------
collect_metrics() {
    info "Zbieranie metryk z Prometheusa..."

    local timestamp=$(date +%Y%m%d_%H%M%S)
    local metrics_file="${RESULTS_DIR}/metrics_${timestamp}.txt"

    nohup ${KUBECTL} -n "${NAMESPACE}" port-forward svc/prometheus 9090:9090 &>/tmp/pf-prom.log &
    local prom_pid=$!
    sleep 2

    echo "=== Metryki po testach (${timestamp}) ===" > "${metrics_file}"
    echo "" >> "${metrics_file}"

    # CPU-service custom metrics
    echo "--- cpu_service_images_processed_total ---" >> "${metrics_file}"
    curl -s 'http://localhost:9090/api/v1/query?query=cpu_service_images_processed_total' 2>/dev/null >> "${metrics_file}" || true

    echo "" >> "${metrics_file}"
    echo "--- cpu_service_http_requests_per_second (per pod) ---" >> "${metrics_file}"
    curl -s 'http://localhost:9090/api/v1/query?query=sum(rate(cpu_service_http_requests_total[5m]))by(pod)' 2>/dev/null >> "${metrics_file}" || true

    # IO-service custom metrics
    echo "" >> "${metrics_file}"
    echo "--- io_service_queries_total ---" >> "${metrics_file}"
    curl -s 'http://localhost:9090/api/v1/query?query=io_service_queries_total' 2>/dev/null >> "${metrics_file}" || true

    echo "" >> "${metrics_file}"
    echo "--- io_service_http_requests_per_second (per pod) ---" >> "${metrics_file}"
    curl -s 'http://localhost:9090/api/v1/query?query=sum(rate(io_service_http_requests_total[5m]))by(pod)' 2>/dev/null >> "${metrics_file}" || true

    # HPA metrics
    echo "" >> "${metrics_file}"
    echo "--- kube_hpa_status_current_replicas ---" >> "${metrics_file}"
    curl -s 'http://localhost:9090/api/v1/query?query=kube_hpa_status_current_replicas' 2>/dev/null >> "${metrics_file}" || true

    echo "" >> "${metrics_file}"
    echo "--- kube_hpa_spec_target_metric ---" >> "${metrics_file}"
    curl -s 'http://localhost:9090/api/v1/query?query=kube_hpa_spec_target_metric' 2>/dev/null >> "${metrics_file}" || true

    kill "${prom_pid}" 2>/dev/null || true
    ok "Metryki zapisane do ${metrics_file}"
}

# ---- Podsumowanie -----------------------------------------------------------
print_summary() {
    echo ""
    echo "============================================================================"
    echo -e "${GREEN}  FAZA V — BADANIA EKSPERYMENTALNE ZAKOŃCZONE${NC}"
    echo "============================================================================"
    echo ""
    echo "  Wyniki zapisane w: ${RESULTS_DIR}/"
    echo ""
    find "${RESULTS_DIR}" -name "*.txt" -type f 2>/dev/null | sort | while read -r f; do
        echo "  $(wc -l < "${f}" | tr -d ' ') lines — ${f}"
    done
    echo ""
    echo "----------------------------------------------------------------------------"
    echo "  Struktura wyników:"
    echo "  results/"
    echo "    baseline-cpu/     ← CPU-bound bez HPA"
    echo "    baseline-io/      ← I/O-bound bez HPA"
    echo "    cpu-cpu/          ← CPU-bound + HPA CPU"
    echo "    cpu-memory/       ← CPU-bound + HPA Memory"
    echo "    cpu-custom/       ← CPU-bound + HPA Custom"
    echo "    io-cpu/           ← I/O-bound + HPA CPU"
    echo "    io-memory/        ← I/O-bound + HPA Memory"
    echo "    io-custom/        ← I/O-bound + HPA Custom"
    echo "    metrics_*.txt     ← Snapshot metryk Prometheusa"
    echo "----------------------------------------------------------------------------"
    echo ""
    echo "  Kluczowe metryki z plików .txt (k6 end-of-test summary):"
    echo "  - http_req_duration (avg, p(95), p(99))"
    echo "  - http_req_failed (rate)"
    echo "  - iterations / VUs"
    echo "  - data_received / data_sent"
    echo "----------------------------------------------------------------------------"
}

# ---- Wszystkie scenariusze (macierz 2×3) -----------------------------------
run_all() {
    info "===== URUCHAMIANIE WSZYSTKICH SCENARIUSZY (macierz 2×3) ====="
    echo ""
    info "Całkowity czas: ~72 min (8 scenariuszy × 5 powtórzeń × ~1 min + cooldowny)"
    echo ""

    # Baseline
    scenario_baseline_cpu
    cooldown
    scenario_baseline_io
    cooldown

    # CPU-bound × 3 strategie
    scenario_cpu_cpu
    cooldown
    scenario_cpu_memory
    cooldown
    scenario_cpu_custom
    cooldown

    # I/O-bound × 3 strategie
    scenario_io_cpu
    cooldown
    scenario_io_memory
    cooldown
    scenario_io_custom

    ok "Wszystkie 8 scenariuszy zakończonych"
}

# ---- Main -------------------------------------------------------------------
main() {
    mkdir -p "${RESULTS_DIR}"

    echo ""
    echo "============================================================================"
    echo "  Magisterka — Faza V: Badania Eksperymentalne (k6 Load Tests)"
    echo "  Macierz 2×3: CPU-bound, I/O-bound × CPU, Memory, Custom HPA"
    echo "============================================================================"
    echo ""

    check_prereqs

    local mode="${1:-all}"

    case "${mode}" in
        all)
            start_port_forwards
            run_all
            stop_port_forwards
            collect_metrics
            print_summary
            ;;
        baseline-cpu)
            start_port_forwards
            scenario_baseline_cpu
            stop_port_forwards
            ;;
        baseline-io)
            start_port_forwards
            scenario_baseline_io
            stop_port_forwards
            ;;
        cpu-cpu)
            start_port_forwards
            scenario_cpu_cpu
            stop_port_forwards
            ;;
        cpu-memory)
            start_port_forwards
            scenario_cpu_memory
            stop_port_forwards
            ;;
        cpu-custom)
            start_port_forwards
            scenario_cpu_custom
            stop_port_forwards
            ;;
        io-cpu)
            start_port_forwards
            scenario_io_cpu
            stop_port_forwards
            ;;
        io-memory)
            start_port_forwards
            scenario_io_memory
            stop_port_forwards
            ;;
        io-custom)
            start_port_forwards
            scenario_io_custom
            stop_port_forwards
            ;;
        quick)
            start_port_forwards
            test_quick
            stop_port_forwards
            ;;
        preflight)
            test_preflight
            ;;
        metrics)
            collect_metrics
            ;;
        stop)
            stop_port_forwards
            ;;
        *)
            echo "Użycie: $0 [all|baseline-cpu|baseline-io|cpu-cpu|cpu-memory|cpu-custom|io-cpu|io-memory|io-custom|quick|preflight|metrics|stop]"
            echo ""
            echo "  all          — wszystkie 8 scenariuszy (macierz 2×3, ~48 min)"
            echo "  baseline-cpu — CPU-bound bez HPA (~6 min)"
            echo "  baseline-io  — I/O-bound bez HPA (~6 min)"
            echo "  cpu-cpu      — CPU-bound + HPA CPU (~6 min)"
            echo "  cpu-memory   — CPU-bound + HPA Memory (~6 min)"
            echo "  cpu-custom   — CPU-bound + HPA Custom RPS (~6 min)"
            echo "  io-cpu       — I/O-bound + HPA CPU (~6 min)"
            echo "  io-memory    — I/O-bound + HPA Memory (~6 min)"
            echo "  io-custom    — I/O-bound + HPA Custom RPS (~6 min)"
            echo "  quick        — szybki smoke test (~1 min)"
            echo "  preflight    — ~5 min pre-flight test z HPA scale-up + Grafana"
            echo "  metrics      — tylko zbieranie metryk Prometheusa"
            echo "  stop         — zatrzymaj port-forwardy"
            exit 1
            ;;
    esac
}

main "$@"
