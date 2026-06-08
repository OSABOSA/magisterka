# PRD: System do Badania Strategii HPA na Kubernetes (Magisterka)

> **Data:** 2026-05-24 &nbsp;|&nbsp; **Commit:** 720c373 &nbsp;|&nbsp; **Język:** polski
>
> Niniejszy dokument stanowi specyfikację produktową projektu magisterskiego
> „Projekt i analiza porównawcza wydajności skalowalnych architektur systemów typu SaaS".
> Służy jako podstawa do oszacowania kosztów chmury (Azure/GCP) dla Fazy VII.

---

## Spis treści

1. [Problem Statement](#1-problem-statement)
2. [Solution](#2-solution)
3. [Architektura Systemu](#3-architektura-systemu)
4. [Charakterystyka Komponentów](#4-charakterystyka-komponentów)
5. [Matryca Badawcza i Scenariusze Testowe](#5-matryca-badawcza-i-scenariusze-testowe)
6. [Wymiarowanie Infrastruktury Chmurowej](#6-wymiarowanie-infrastruktury-chmurowej)
7. [Szacunkowe Koszty Chmury](#7-szacunkowe-koszty-chmury)
8. [Oczekiwane Zachowanie HPA w Scenariuszach](#8-oczekiwane-zachowanie-hpa-w-scenariuszach)
9. [Harmonogram Wykonania](#9-harmonogram-wykonania)
10. [Wymagania Wstępne](#10-wymagania-wstępne)
11. [Decyzje Implementacyjne](#11-decyzje-implementacyjne)
12. [Poza Zakresem](#12-poza-zakresem)
13. [Uwagi Końcowe](#13-uwagi-końcowe)

---

## 1. Problem Statement

**Z perspektywy badacza-inżyniera:** potrzebuję środowiska eksperymentalnego, które pozwoli
mi empirycznie zweryfikować hipotezę, że **nie istnieje jedna uniwersalna metryka HPA** —
skuteczność strategii skalowania [`HorizontalPodAutoscaler`](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/)
zależy od charakterystyki obciążeniowej aplikacji (workload profilu). Błędny wybór metryki
prowadzi do niedoskalowania (ang. _underscaling_) lub przeskalowania (ang. _overscaling_).

Aby przeprowadzić kontrolowane eksperymenty, potrzebuję:

- Dwu aplikacji o diametralnie różnych profilach obciążeniowych (CPU-bound vs I/O-bound)
- Trzech strategii HPA (Resource CPU, Resource Memory, Custom RPS)
- Pełnego monitoringu (Prometheus + Grafana) umożliwiającego pomiar metryk badawczych
- Zautomatyzowanego harnessu testowego (k6) odtwarzającego identyczne obciążenie
  we wszystkich scenariuszach
- Środowiska możliwego do odtworzenia w chmurze publicznej (Azure/GCP) dla celów
  porównawczych (lokalny k3s vs managed Kubernetes)

**Z perspektywy kosztowej:** potrzebuję oszacowania rozmiaru maszyny wirtualnej w chmurze,
która pomieści cały stos (3 serwisy aplikacyjne + monitoring + adapter HPA) na czas
trwania eksperymentów (~2–3 godziny), tak aby zminimalizować koszty przy zachowaniu
powtarzalności wyników.

---

## 2. Solution

System składa się z:

1. **Dwóch mikroserwisów FastAPI** (`cpu-service`, `io-service`) — każdy z osobnym profilem
   obciążeniowym, eksponujących endpointy HTTP i metryki Prometheus. Trzeci serwis
   (`echo-server`) pełni rolę pomocniczą jako cel zewnętrznych wywołań HTTP.

2. **Klastra Kubernetes (k3s)** — jednawęzłowego, z wdrożonymi manifestami Deployment,
   Service i trzema wariantami HPA dla każdego serwisu aplikacyjnego.

3. **Stosu monitorującego** — Prometheus (scrapowanie metryk z aplikacji i K8s),
   Grafana (dashboard `magisterka-overview`), `prometheus-adapter` (mostek między
   Prometheus a HPA Custom Metrics API).

4. **Zautomatyzowanego harnessu testowego** — skrypty [`k6`](https://k6.io) dla CPU-bound
   i I/O-bound, skrypt [`run-tests.sh`](scripts/run-tests.sh) automatyzujący wykonanie
   całej macierzy 2×3 z 5 powtórzeniami na scenariusz.

5. **Skryptu deployującego** [`deploy-k3s.sh`](scripts/deploy-k3s.sh) — buduje obrazy
   Docker, importuje do containerd k3s, nakłada manifesty, czeka na gotowość.

Wynikiem działania systemu jest zbiór danych pomiarowych (`results/<scenariusz>/run-N/`)
zawierający surowe wyniki k6, metadane uruchomień oraz snapshoty metryk Prometheusa.

---

## 3. Architektura Systemu

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          KLASTER KUBERNETES (k3s)                         │
│                          Namespace: magisterka                            │
│                                                                           │
│  ┌──────────────────────┐  ┌──────────────────────┐  ┌────────────────┐  │
│  │    cpu-service        │  │    io-service         │  │  echo-server   │  │
│  │  ┌────────────────┐  │  │  ┌────────────────┐  │  │                │  │
│  │  │ Deployment     │  │  │  │ Deployment     │  │  │ Deployment     │  │
│  │  │ replicas: 2-10 │  │  │  │ replicas: 2-10 │  │  │ replicas: 1    │  │
│  │  │ cpu req: 250m  │  │  │  │ cpu req: 100m  │  │  │ cpu req: 50m   │  │
│  │  │ mem req: 256Mi │  │  │  │ mem req: 128Mi │  │  │ mem req: 64Mi  │  │
│  │  └───────┬────────┘  │  │  └───────┬────────┘  │  │                │  │
│  │          │            │  │          │            │  │ Port: 8000     │  │
│  │  ┌───────┴────────┐  │  │  ┌───────┴────────┐  │  └───────┬────────┘  │
│  │  │ HPA x3:        │  │  │  │ HPA x3:        │  │          │            │
│  │  │  • CPU (50%)   │  │  │  │  • CPU (50%)   │  │          │            │
│  │  │  • Mem (70%)   │  │  │  │  • Mem (70%)   │  │          │            │
│  │  │  • Custom RPS  │  │  │  │  • Custom RPS  │  │          │            │
│  │  └────────────────┘  │  │  └────────────────┘  │          │            │
│  │                      │  │                      │          │            │
│  │  Endpoints:          │  │  Endpoints:          │  │  /health          │
│  │  GET /health         │  │  GET /health         │  │  /metrics         │
│  │  GET /metrics        │  │  GET /metrics        │  │  /upstream        │
│  │  GET /fibonacci?n=N  │  │  GET /query?delay=N  │  │  POST /echo       │
│  │  POST /process       │  │       &steps=N       │  │                   │
│  │                      │  │       &external_call │  │                   │
│  │                      │  │  GET /upstream       │  │                   │
│  └──────────┬───────────┘  └──────────┬───────────┘  └──────────┬────────┘
│             │                          │                          │
│             │  ClusterIP:80            │  ClusterIP:80            │  ClusterIP:80
│             ▼                          ▼                          ▼
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                         WARSTWA MONITORINGU                            │  │
│  │                                                                        │  │
│  │  ┌──────────────────┐  ┌──────────────┐  ┌──────────────────────────┐ │  │
│  │  │   Prometheus     │  │   Grafana    │  │  prometheus-adapter      │ │  │
│  │  │   :9090          │  │   :3000      │  │  :443 (APIService)       │ │  │
│  │  │   req: 250m/512M │  │   req: 100m  │  │  req: 100m/128Mi         │ │  │
│  │  │   lim: 500m/1Gi  │  │       /256Mi │  │  lim: 500m/512Mi         │ │  │
│  │  │   emptyDir TSDB  │  │   lim: 500m  │  │                          │ │  │
│  │  │                  │  │       /512Mi │  │  Reguły:                 │ │  │
│  │  │  scrape: 15s     │  │   emptyDir   │  │  cpu/io RPS → custom     │ │  │
│  │  │  targets:        │  │              │  │  metrics API             │ │  │
│  │  │   cpu-service    │  │  Dashboard:  │  │                          │ │  │
│  │  │   io-service     │  │  magisterka- │  │                          │ │  │
│  │  │   echo-service   │  │  overview    │  │                          │ │  │
│  │  └──────────────────┘  └──────────────┘  └──────────────────────────┘ │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
                                   │
                                   │ port-forward (localhost)
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                           HOST (VM / QNAP)                                │
│                                                                           │
│  ┌─────────────┐     ┌───────────────────────────────────────────────┐   │
│  │     k6      │────▶│  localhost:8080 (cpu-service port-forward)    │   │
│  │  load gen   │     │  localhost:8081 (io-service port-forward)     │   │
│  └─────────────┘     │  localhost:3000 (Grafana port-forward)        │   │
│                      └───────────────────────────────────────────────┘   │
│                                                                           │
│  Skrypty:                                                                 │
│  • deploy-k3s.sh  – budowa obrazów + deployment na k3s                   │
│  • run-tests.sh   – orkiestracja 8 scenariuszy × 5 powtórzeń             │
│  • run-all.sh     – wrapper: deploy → testy                              │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Charakterystyka Komponentów

### 4.1. cpu-service — Obciążenie CPU-bound

**Technologia:** Python 3.12 + FastAPI + Uvicorn + Pillow  
**Obraz Docker:** [`apps/cpu-service/Dockerfile`](apps/cpu-service/Dockerfile)

| Właściwość | Wartość |
|---|---|
| Port kontenera | `8000` |
| CPU request / limit | `250m` / `1000m` (1 vCPU) |
| Memory request / limit | `256Mi` / `512Mi` |
| Repliki (domyślnie) | 2 |
| Repliki (min–max HPA) | 2–10 |
| Metryki Prometheus | `/metrics` (auto-instrumentowane + niestandardowe liczniki) |

**Endpointy:**

| Metoda | Ścieżka | Opis | Złożoność |
|---|---|---|---|
| `GET` | `/health` | Health check (liveness/readiness) | O(1) |
| `GET` | `/metrics` | Metryki Prometheus | O(1) |
| `GET` | `/fibonacci?n=25` | Rekurencyjny Fibonacci, O(2^n), czyste CPU | O(2^n) |
| `POST` | `/process` | Przetwarzanie obrazu (resize + filtr + histogram + entropia) | CPU-intensywne |

**Profil obciążeniowy przy teście k6 (50 VU, `n=25`):**

- CPU: ~80–100% na Pod
- RAM: stabilne, ~80–120 Mi na Pod (Pillow alokuje bufory)
- I/O wait: zerowy (brak wywołań sieciowych w ścieżce `/fibonacci`)

**Niestandardowe metryki Prometheus:**

| Nazwa | Typ | Opis |
|---|---|---|
| `cpu_service_images_processed_total` | Counter | Liczba przetworzonych obrazów |
| `cpu_service_processing_duration_seconds` | Histogram | Czas przetwarzania obrazu |
| `cpu_service_http_requests_inprogress` | Gauge | Liczba obsługiwanych żądań HTTP |

### 4.2. io-service — Obciążenie I/O-bound

**Technologia:** Python 3.12 + FastAPI + Uvicorn + httpx  
**Obraz Docker:** [`apps/io-service/Dockerfile`](apps/io-service/Dockerfile)

| Właściwość | Wartość |
|---|---|
| Port kontenera | `8000` |
| CPU request / limit | `100m` / `500m` |
| Memory request / limit | `128Mi` / `256Mi` |
| Repliki (domyślnie) | 2 |
| Repliki (min–max HPA) | 2–10 |
| Limit współbieżności | `asyncio.Semaphore(50)` |
| Metryki Prometheus | `/metrics` |

**Endpointy:**

| Metoda | Ścieżka | Opis |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/metrics` | Metryki Prometheus |
| `GET` | `/query?delay=50&steps=1&external_call=false` | Łańcuch I/O-bound: N kroków po X ms (±20% jitter) |
| `GET` | `/upstream?delay=N` | Symulacja zewnętrznego serwisu (asyncio.sleep) |

**Profil obciążeniowy przy teście k6 (50 VU, `delay=50, steps=1, external_call=false`):**

- CPU: ~5–15% na Pod (głównie overhead asyncio + FastAPI)
- RAM: stabilne, ~50–80 Mi na Pod
- I/O wait: dominujący — setki równoczesnych korutyn w `asyncio.sleep()`

**Niestandardowe metryki Prometheus:**

| Nazwa | Typ | Opis |
|---|---|---|
| `io_service_queries_total` | Counter | Liczba wywołań `/query` |
| `io_service_upstream_duration_seconds` | Histogram | Czas trwania wywołań upstream |
| `io_service_concurrent_queries` | Gauge | Liczba aktualnie przetwarzanych zapytań |
| `io_service_http_requests_inprogress` | Gauge | Liczba obsługiwanych żądań HTTP |

### 4.3. echo-server — Serwis Pomocniczy

**Technologia:** Python 3.12 + FastAPI + Uvicorn  
**Obraz Docker:** [`apps/echo-server/Dockerfile`](apps/echo-server/Dockerfile)

| Właściwość | Wartość |
|---|---|
| Port kontenera | `8000` |
| CPU request / limit | `50m` / `200m` |
| Memory request / limit | `64Mi` / `128Mi` |
| Repliki | 1 (stałe, bez HPA) |

**Endpointy:** `GET /health`, `GET /metrics`, `GET /upstream` (zwraca tożsamość + timestamp + UUID), `POST /echo`

**Rola:** Cel zewnętrznych wywołań HTTP dla `io-service` gdy `external_call=true`. W testach Fazy V parametr `external_call` jest ustawiony na `false` (używane jest `asyncio.sleep`), więc echo-server nie jest obciążany podczas głównej macierzy testowej.

### 4.4. Stos Monitorujący

#### Prometheus

| Właściwość | Wartość |
|---|---|
| Obraz | `prometheus/prometheus:latest` |
| CPU request / limit | `250m` / `500m` |
| Memory request / limit | `512Mi` / `1024Mi` (1 Gi) |
| Repliki | 1 |
| Przechowywanie danych | `emptyDir` (ulotne) |
| Interwał scrapowania | `15s` |
| Scrapowane cele | `cpu-service`, `io-service`, `echo-service`, `prometheus` |

> **Uwaga:** Prometheus używa `emptyDir`, więc dane TSDB nie są trwałe między restartami.
> Dla eksperymentu trwającego ~2–3h jest to akceptowalne — wszystkie dane są zbierane
> przez k6 i zapisywane do `results/` na hoście.

#### Grafana

| Właściwość | Wartość |
|---|---|
| Obraz | `grafana/grafana:latest` |
| CPU request / limit | `100m` / `500m` |
| Memory request / limit | `256Mi` / `512Mi` |
| Repliki | 1 |
| Dashboard | `Magisterka — System Overview` (uid: `magisterka-overview`) |
| Anonimowy dostęp | włączony |

**Panele dashboardu (16 paneli):**

| # | Tytuł | Typ |
|---|---|---|
| 1 | CPU Service RPS | Stat |
| 2 | IO Service RPS | Stat |
| 4 | CPU Service — CPU Usage (per pod) | Timeseries |
| 5 | CPU Service — Request Latency p50/p95/p99 | Timeseries |
| 6 | CPU Service — Throughput RPS | Timeseries |
| 7 | CPU Service — Images Processed | Timeseries |
| 8 | IO Service — CPU Usage (per pod) | Timeseries |
| 9 | IO Service — Request Latency p50/p95/p99 | Timeseries |
| 10 | IO Service — Concurrent Queries | Timeseries |
| 11 | IO Service — Upstream Duration p50/p95/p99 | Timeseries |
| 16 | Number of Pods per Deployment | Timeseries |
| 17 | HPA — Desired vs Current Replicas | Timeseries |
| 18 | Scaling Events Timeline | Timeseries |

#### Prometheus Adapter

| Właściwość | Wartość |
|---|---|
| Obraz | `registry.k8s.io/prometheus-adapter/prometheus-adapter:v0.12.0` |
| CPU request / limit | `100m` / `500m` |
| Memory request / limit | `128Mi` / `512Mi` |
| Repliki | 1 |
| Interwał przeładowania metryk | `30s` |
| Tryb logowania | `--v=4` (verbose) |

**Reguły mapowania metryk (z `prometheus-adapter-config`):**

- `cpu_service_http_requests_total` → `http_requests_per_second` (rate 1m, sum by pod)
- `cpu_service_http_requests_inprogress` → `http_requests_inflight` (sum by pod)
- `io_service_http_requests_total` → `http_requests_per_second` (rate 1m, sum by pod)
- `io_service_http_requests_inprogress` → `http_requests_inflight` (sum by pod)
- `container_*` → passthrough resource metrics

---

## 5. Matryca Badawcza i Scenariusze Testowe

### 5.1. Hipoteza

|  | HPA CPU (50%) | HPA Memory (70%) | HPA Custom (RPS, 100/Pod) |
|---|---|---|---|
| **CPU-bound** | ✅ Optymalna — CPU rośnie z obciążeniem | ❌ Nie widzi przeciążenia — RAM stabilne | ⚠️ Działa, ale wolniejsza reakcja |
| **I/O-bound** | ❌ Nie widzi przeciążenia — CPU niskie | ❌ Nie widzi przeciążenia — RAM stabilne | ✅ Optymalna — RPS rośnie z obciążeniem |

### 5.2. Scenariusze Testowe

| # | Identyfikator | Workload | Strategia HPA | Skrypt k6 | Powtórzeń | Czas (per run) |
|---|---|---|---|---|---|---|
| 1 | `baseline-cpu` | CPU-bound | Brak (1 replika) | `cpu-load-test.js` | 5 | ~4.5 min |
| 2 | `baseline-io` | I/O-bound | Brak (1 replika) | `io-load-test.js` | 5 | ~4.5 min |
| 3 | `cpu-cpu` | CPU-bound | CPU (50%) | `cpu-load-test.js` | 5 | ~4.5 min |
| 4 | `cpu-memory` | CPU-bound | Memory (70%) | `cpu-load-test.js` | 5 | ~4.5 min |
| 5 | `cpu-custom` | CPU-bound | Custom RPS (100/Pod) | `cpu-load-test.js` | 5 | ~4.5 min |
| 6 | `io-cpu` | I/O-bound | CPU (50%) | `io-load-test.js` | 5 | ~4.5 min |
| 7 | `io-memory` | I/O-bound | Memory (70%) | `io-load-test.js` | 5 | ~4.5 min |
| 8 | `io-custom` | I/O-bound | Custom RPS (100/Pod) | `io-load-test.js` | 5 | ~4.5 min |
| 9 | `preflight` | CPU-bound | CPU (50%) | `preflight-test.js` (inline) | 1 | ~4.5 min |
| 10 | `combined` | CPU+I/O równolegle | CPU | `combined-scenario.js` | 5 | ~5 min |

### 5.3. Profil Obciążenia k6

**Wspólny profil dla wszystkich scenariuszy (ramping-vus):**

| Faza | Czas trwania | Wirtualni Użytkownicy (VU) |
|---|---|---|
| Ramp-up | 90 s | 1 → 50 |
| Steady state | 120 s | 50 |
| Ramp-down | 60 s | 50 → 0 |
| **Łącznie** | **270 s (4.5 min)** | |

**Endpointy testowe:**

- **CPU-bound:** `GET /fibonacci?n=25` — czyste CPU, O(2^n), ~1–5 ms na wywołanie przy braku obciążenia
- **I/O-bound:** `GET /query?delay=50&steps=1&external_call=false` — symulowane I/O, ~50 ms na krok (+ jitter ±20%)

**Progi (thresholds) k6:**

- CPU: `p(95) < 10 s`, `http_req_failed < 10%`
- I/O: `p(95) < 5 s`, `http_req_failed < 10%`

### 5.4. Metryki Badawcze

| Metryka | Definicja | Jednostka | Źródło |
|---|---|---|---|
| **Latency (p50, p95, p99)** | Czas odpowiedzi — percentyle | ms | k6 + Prometheus |
| **Throughput** | Łączna liczba obsłużonych żądań na sekundę | RPS | k6 + Prometheus |
| **Time-to-scale** | Czas od przekroczenia progu HPA do gotowości nowego Pod-a | s | Prometheus + metryki K8s |
| **Efektywność skalowania** | Throughput na replikę (RPS / liczba Pod-ów) | RPS/Pod | Obliczeniowa |
| **Error rate** | Odsetek żądań zakończonych błędem (4xx, 5xx, timeout) | % | k6 |

### 5.5. Szacowany Czas Wykonania Pełnego Eksperymentu

| Element | Czas |
|---|---|
| 8 scenariuszy × 5 powtórzeń × 4.5 min | 180 min (3 h) |
| 7 okresów cooldown (120 s każdy) | 14 min |
| 1 × preflight (1 powtórzenie) | 5 min |
| 1 × combined (5 powtórzeń) | 25 min |
| **Łącznie czas testów** | **~224 min (~3.7 h)** |
| Setup + deployment | ~30 min |
| **Całkowity czas życia VM** | **~4–5 h** |

> **Uwaga:** W praktyce skrypt [`run-tests.sh`](scripts/run-tests.sh) uruchamia 8 scenariuszy
> głównych (bez combined), co daje ~72 min + cooldowny. Doliczając preflight i combined,
> bezpieczny bufor to 5 godzin pracy maszyny.

---

## 6. Wymiarowanie Infrastruktury Chmurowej

### 6.1. Sumaryczne Zapotrzebowanie na Zasoby (Klaster)

Poniższe wartości reprezentują **sumę requestów** wszystkich Podów w stanie ustalonym.
Wartości `limit` są maksymalnym potencjalnym zużyciem.

#### Stan bazowy (wszystkie Pody + monitoring)

| Komponent | Repliki | CPU Request | CPU Limit | Mem Request | Mem Limit |
|---|---|---|---|---|---|
| `cpu-service` | 2 | 500m | 2000m | 512Mi | 1024Mi |
| `io-service` | 2 | 200m | 1000m | 256Mi | 512Mi |
| `echo-server` | 1 | 50m | 200m | 64Mi | 128Mi |
| `prometheus` | 1 | 250m | 500m | 512Mi | 1024Mi |
| `grafana` | 1 | 100m | 500m | 256Mi | 512Mi |
| `prometheus-adapter` | 1 | 100m | 500m | 128Mi | 512Mi |
| **Suma (request)** | — | **1200m** | — | **1728Mi (~1.7 Gi)** | — |
| **Suma (limit)** | — | — | **4700m** | — | **3712Mi (~3.6 Gi)** |

#### Stan szczytowy (cpu-service lub io-service przy maxReplicas=10)

| Komponent | Repliki | CPU Request | CPU Limit | Mem Request | Mem Limit |
|---|---|---|---|---|---|
| `cpu-service` (max) | 10 | 2500m | 10000m | 2560Mi | 5120Mi |
| Pozostałe (jak wyżej) | 5 | 650m | 2700m | 1216Mi | 2688Mi |
| **Suma (request)** | — | **3150m** | — | **3776Mi (~3.7 Gi)** | — |
| **Suma (limit)** | — | — | **12700m** | — | **7808Mi (~7.6 Gi)** |

> **Kluczowa obserwacja:** Suma CPU limitów w stanie szczytowym (12.7 vCPU) przewyższa
> fizyczne CPU węzła. Jest to akceptowalne — Kubernetes pozwala na overcommit CPU;
> limity CPU są _throttlingiem_, nie rezerwacją. Rzeczywiste zużycie CPU będzie bliższe
> sumie requestów (~3.2 vCPU), ponieważ tylko cpu-service faktycznie używa CPU.

### 6.2. Rekomendowane Rozmiary VM

#### Azure

| SKU | vCPU | RAM | Dysk | Szacunkowy koszt/h | Ocena |
|---|---|---|---|---|---|
| **Standard_D2s_v5** | 2 | 8 GiB | 30 GB SSD | ~$0.10 | ⚠️ Minimalne — ryzyko OOM przy max replikach |
| **Standard_D4s_v5** | 4 | 16 GiB | 30 GB SSD | ~$0.20 | ✅ Rekomendowane — komfortowy zapas |
| **Standard_D2as_v5** (AMD) | 2 | 8 GiB | 30 GB SSD | ~$0.09 | ⚠️ Tańszy wariant minimalny |
| **Standard_D4as_v5** (AMD) | 4 | 16 GiB | 30 GB SSD | ~$0.17 | ✅ Rekomendowany AMD |

**Rekomendacja Azure:** `Standard_D4s_v5` (4 vCPU, 16 GiB RAM) — **~$0.20/h**
- Koszt całkowity dla 5h: **~$1.00**
- Koszt całkowity dla 8h (bufor): **~$1.60**

#### GCP

| SKU | vCPU | RAM | Dysk | Szacunkowy koszt/h | Ocena |
|---|---|---|---|---|---|
| **e2-standard-2** | 2 | 8 GiB | 30 GB SSD | ~$0.07 | ⚠️ Minimalne — ryzyko OOM |
| **e2-standard-4** | 4 | 16 GiB | 30 GB SSD | ~$0.15 | ✅ Rekomendowane |
| **n2-standard-2** | 2 | 8 GiB | 30 GB SSD | ~$0.10 | ⚠️ Minimalne, lepsza wydajność CPU |
| **n2-standard-4** | 4 | 16 GiB | 30 GB SSD | ~$0.20 | ✅ Rekomendowane z lepszym CPU |

**Rekomendacja GCP:** `e2-standard-4` (4 vCPU, 16 GiB RAM) — **~$0.15/h**
- Koszt całkowity dla 5h: **~$0.75**
- Koszt całkowity dla 8h (bufor): **~$1.20**

> **Uwaga:** GCP oferuje $300 kredytów na start (90 dni). Przy rekomendowanej maszynie
> e2-standard-4 i 5h użycia, koszt $0.75 jest pomijalny. Większym kosztem może być
> GKE (managed Kubernetes), jeśli zostanie użyty zamiast k3s na gołej VM.

### 6.3. Wariant z Managed Kubernetes (GKE / AKS)

Jeśli Faza VII wymaga **managed Kubernetes** (GKE/AKS) zamiast k3s na gołej VM:

#### GCP GKE

| Komponent | Specyfikacja | Koszt/h |
|---|---|---|
| Cluster management fee | 1 klaster (zonal) | $0.10/h |
| Node pool: 1× e2-standard-4 | 4 vCPU, 16 GiB | $0.15/h |
| **Suma GKE** | | **~$0.25/h** |
| Koszt całkowity (5h) | | **~$1.25** |

#### Azure AKS

| Komponent | Specyfikacja | Koszt/h |
|---|---|---|
| Cluster management | 1 klaster (free tier) | $0.00/h |
| Node pool: 1× Standard_D4s_v5 | 4 vCPU, 16 GiB | $0.20/h |
| **Suma AKS** | | **~$0.20/h** |
| Koszt całkowity (5h) | | **~$1.00** |

> **Rekomendacja:** Dla Fazy VII (porównanie lokalny k3s vs chmura) preferowany jest
> GCP GKE ze względu na $300 kredytów startowych i prostszą konfigurację. Koszt
> eksperymentu (~$1.25) jest pomijalny.

### 6.4. Dysk i Storage

| Zasób | Rozmiar | Uwagi |
|---|---|---|
| System operacyjny + Docker + k3s | ~15 GB | Ubuntu Server 22.04 LTS |
| Obrazy kontenerów (3× Python-based) | ~2 GB | łącznie |
| Prometheus TSDB (emptyDir, ulotne) | ~2–5 GB | rośnie z czasem, max ~5 GB przy 5h |
| Wyniki testów (k6 JSON + logi) | ~1 GB | 10 scenariuszy × 5 powtórzeń × ~2 MB |
| **Rekomendowany dysk** | **30 GB SSD** | Standardowy dla większości VM |


### 6.5. Sieć

| Aspekt | Wartość |
|---|---|
| Ingress | **Nieużywany** — testy przez localhost port-forward |
| Ruch wychodzący | Minimalny (tylko SSH + ewentualne `apt update`) |
| k6 ↔ serwisy | localhost (port-forward), zerowy koszt sieci |
| Publiczny IP | 1 (dla SSH) |
| Load balancer | Niepotrzebny |

---

## 7. Szacunkowe Koszty Chmury — Tabela Porównawcza

| Dostawca | Konfiguracja | vCPU | RAM | Dysk | $/h | 5h łącznie |
|---|---|---|---|---|---|---|
| **GCP** | e2-standard-4 + boot 30GB | 4 | 16 GiB | 30 GB SSD | $0.15 | **$0.75** |
| **GCP GKE** | e2-standard-4 + management fee | 4 | 16 GiB | 30 GB SSD | $0.25 | **$1.25** |
| **Azure** | Standard_D4s_v5 + boot 30GB | 4 | 16 GiB | 30 GB SSD | $0.20 | **$1.00** |
| **Azure** | Standard_D2s_v5 + boot 30GB | 2 | 8 GiB | 30 GB SSD | $0.10 | **$0.50** ⚠️ |
| **Azure AKS** | Standard_D4s_v5 (free tier) | 4 | 16 GiB | 30 GB SSD | $0.20 | **$1.00** |

> ⚠️ Wariant 2-vCPU/8GB jest ryzykowny — przy 10 replikach cpu-service suma memory
> requestów to ~3.7 Gi, a dochodzi jeszcze overhead k3s + systemu operacyjnego (~1.5 GB).
> Łącznie ~5.2 Gi zajęte, zostaje ~2.8 Gi wolne. Prometheus może w szczycie alokować
> dodatkowe ~500 Mi na zapytania PromQL. **Ryzyko OOM Kill jest realne.**

---

## 8. Oczekiwane Zachowanie HPA w Scenariuszach

### 8.1. `baseline-cpu` — CPU-bound, brak HPA, 1 replika

- **Oczekiwane zachowanie:** 1 Pod, CPU ~100%, kolejka żądań rośnie
- **Latency:** p95 gwałtownie rośnie w fazie steady (50 VU × n=25 → 1 Pod nie wyrabia)
- **Error rate:** może pojawić się timeout przy 50 VU na 1 Podzie
- **Cel badawczy:** Punkt referencyjny — jak źle jest bez skalowania

### 8.2. `baseline-io` — I/O-bound, brak HPA, 1 replika

- **Oczekiwane zachowanie:** 1 Pod, CPU niskie (~10%), ale semafor (max 50 współbieżnych)
  ogranicza przepustowość. Przy 50 VU z `asyncio.sleep(50ms)` Pod powinien dawać radę,
  ale latency rośnie liniowo z liczbą VU.
- **Cel badawczy:** Referencja I/O-bound

### 8.3. `cpu-cpu` — CPU-bound + HPA CPU (50%)

- **Oczekiwane zachowanie:** ✅ **Optymalne.** CPU rośnie z obciążeniem → HPA wykrywa
  przekroczenie 50% → skaluje w górę. Przy 50 VU i n=25, 2 Pod-y mają ~100% CPU każdy,
  HPA powinien dodać kolejne. Oczekiwane ~6–8 Pod-ów w fazie steady.
- **Time-to-scale:** ~60–90 s od przekroczenia progu (domyślny `--horizontal-pod-autoscaler-sync-period` = 15s + cooldown 30s + czas uruchomienia Pod-a ~15–30s)
- **Efektywność skalowania:** Wysoka — każdy dodatkowy Pod proporcjonalnie zwiększa throughput

### 8.4. `cpu-memory` — CPU-bound + HPA Memory (70%)

- **Oczekiwane zachowanie:** ❌ **Nieskuteczne.** RAM cpu-service jest stabilne (~80–120 Mi
  na Pod, przy limicie 512Mi to ~16–23% utilization). HPA Memory nigdy nie osiągnie progu 70%,
  więc nigdy nie przeskaluje powyżej minReplicas=2.
- **Liczba Pod-ów:** 2 (przez cały test)
- **Latency:** Podobne do baseline (2 Pod-y vs 1 to niewielka poprawa)
- **Cel badawczy:** Dowód, że Memory-HPA nie nadaje się do CPU-bound workloadów

### 8.5. `cpu-custom` — CPU-bound + HPA Custom RPS (100/Pod)

- **Oczekiwane zachowanie:** ⚠️ **Działa, ale z opóźnieniem.** RPS rośnie z liczbą VU,
  ale metryka `http_requests_per_second` jest wyliczana jako `rate(...[1m])` — ma
  1-minutowe okno wygładzające. HPA widzi wzrost RPS z ~60s opóźnieniem.
- **Liczba Pod-ów:** Powinna rosnąć, ale wolniej niż przy HPA CPU. Oczekiwane ~4–6 Pod-ów.
- **Time-to-scale:** Dłuższy niż HPA CPU (~90–120s) ze względu na wygładzanie PromQL
- **Cel badawczy:** Porównanie czasu reakcji CPU vs Custom

### 8.6. `io-cpu` — I/O-bound + HPA CPU (50%)

- **Oczekiwane zachowanie:** ❌ **Nieskuteczne.** CPU io-service przy 50 VU i
  `asyncio.sleep(50ms)` utrzymuje się na poziomie ~5–15% — daleko od progu 50%.
  HPA nigdy nie skaluje powyżej minReplicas=2.
- **Liczba Pod-ów:** 2 (przez cały test)
- **Cel badawczy:** Dowód, że CPU-HPA nie nadaje się do I/O-bound workloadów

### 8.7. `io-memory` — I/O-bound + HPA Memory (70%)

- **Oczekiwane zachowanie:** ❌ **Nieskuteczne.** RAM io-service jest stabilne (~50–80 Mi,
  przy limicie 256Mi to ~20–31%). HPA Memory nigdy nie osiągnie progu 70%.
- **Liczba Pod-ów:** 2
- **Cel badawczy:** Drugi dowód nieskuteczności resource-metric HPA dla I/O-bound

### 8.8. `io-custom` — I/O-bound + HPA Custom RPS (100/Pod)

- **Oczekiwane zachowanie:** ✅ **Optymalne.** RPS jest jedyną metryką, która rośnie
  proporcjonalnie do obciążenia w workloadzie I/O-bound. HPA Custom prawidłowo wykrywa
  wzrost RPS i skaluje. Oczekiwane ~4–8 Pod-ów.
- **Time-to-scale:** ~90–120s (opóźnienie PromQL rate 1m)
- **Efektywność skalowania:** Wysoka — każdy Pod dodaje capacity na kolejne współbieżne
  `asyncio.sleep`

### 8.9. `preflight` — CPU-bound + HPA CPU (szybki test weryfikacyjny)

- **Cel:** Potwierdzenie, że HPA CPU poprawnie wykrywa obciążenie i skaluje przed
  rozpoczęciem pełnego eksperymentu.
- Uruchamiany raz, trwa ~4.5 min, z automatycznym port-forward Grafany.
- **Sukces:** HPA pokazuje CURRENT_REPLICAS > 2 w trakcie fazy steady.

### 8.10. `combined` — CPU + I/O równolegle + HPA CPU

- 3 scenariusze k6 jednocześnie: fibonacci (30 VU), processImage (5 VU), ioQuery (80 VU)
- HPA CPU na obu serwisach — oczekiwane skalowanie tylko cpu-service
- **Cel badawczy:** Pokazanie, że w systemie mieszanym HPA CPU chroni CPU-bound,
  ale I/O-bound pozostaje bez ochrony

---

## 9. Harmonogram Wykonania

```
Faza 0     [✓] Minimalny setup (CPU-service + HPA CPU)
Faza I     [✓] Analiza literatury (równolegle z Fazą 0)
Faza II    [✓] Projekt architektury
Faza III   [✓] Implementacja pełnego prototypu (2 aplikacje, 3×HPA)
Faza IV    [✓] Środowisko testowe (deploy-k3s.sh, run-tests.sh)
Faza V     [ ] Badania eksperymentalne — macierz 2×3 na k3s (8 scenariuszy)
Faza VI    [ ] Opracowanie wyników + redakcja pracy
Faza VII   [ ] Rozszerzenie: Chmura (GCP GKE / Azure AKS)  ← TEN PRD
Faza VIII  [ ] Rozszerzenie: SaaS Multi-Tenancy
```

---

## 10. Wymagania Wstępne

### 10.1. Oprogramowanie na maszynie (VM / bare-metal)

| Narzędzie | Minimalna wersja | Uwagi |
|---|---|---|
| **k3s** | ≥ v1.28 | Jednąwęzłowy klaster Kubernetes |
| **kubectl** | ≥ v1.28 | Kompatybilny z wersją k3s |
| **Docker** | ≥ 24.x | Do budowania obrazów |
| **k6** | ≥ v0.48 | Generator obciążenia |
| **curl** | dowolna | Health check |
| **bash** | ≥ 5.x | Do uruchamiania skryptów |

### 10.2. Repozytorium

- [`magisterka/`](.) — kompletny kod źródłowy
- Commit: `720c373` (Faza I–IV kompletna)

### 10.3. Konfiguracja Chmurowa (Faza VII)

1. Utworzenie VM z Ubuntu Server 22.04 LTS (4 vCPU, 16 GB RAM, 30 GB SSD)
2. Instalacja: Docker, k3s, kubectl, k6
3. Sklonowanie repozytorium
4. Uruchomienie `./scripts/deploy-k3s.sh`
5. Uruchomienie `./scripts/run-tests.sh all`
6. Pobranie wyników `results/`
7. **Natychmiastowe skasowanie VM** po zakończeniu testów (uniknięcie naliczania kosztów)

---

## 11. Decyzje Implementacyjne

### 11.1. Moduły Systemu

| Moduł | Interfejs | Testowalność w izolacji |
|---|---|---|
| **cpu-service** | HTTP REST + `/metrics` | ✅ Wysoka — endpoint `/fibonacci?n=N` jest deterministyczny; testowalne `pytest` |
| **io-service** | HTTP REST + `/metrics` | ✅ Wysoka — endpoint `/query` z kontrolowanymi parametrami |
| **echo-server** | HTTP REST + `/metrics` | ✅ Trywialne |
| **HPA manifests** | Kubernetes API (deklaratywne YAML) | ❌ Wymaga klastra K8s |
| **prometheus-adapter config** | ConfigMap → PromQL rules | ⚠️ Można testować PromQL osobno |
| **k6 scripts** | k6 executor API (JS) | ✅ Można odpalić przeciwko lokalnemu `docker compose` |
| **run-tests.sh** | Bash CLI | ⚠️ Testowalny fragmentami (funkcje `switch_hpa`, `reset_deployment`) |
| **deploy-k3s.sh** | Bash CLI | ⚠️ Wymaga k3s |
| **Grafana dashboard** | JSON model | ✅ Import do dowolnej Grafany |

### 11.2. Kluczowe Decyzje Architektoniczne

1. **Jednowęzłowy k3s zamiast wielowęzłowego:** Cel eksperymentu to porównanie strategii
   HPA, nie badanie rozproszenia Pod-ów między węzłami. Jeden węzeł eliminuje zmienne
   zakłócające (network latency między węzłami, topology spread).

2. **Port-forward zamiast Ingress/LoadBalancer:** W środowisku testowym k6 działa na tym
   samym hoście co klaster. Port-forward eliminuje potrzebę zewnętrznego load balancera,
   redukując koszty chmury.

3. **`emptyDir` dla Prometheusa:** Dane TSDB nie muszą być trwałe — wyniki są zbierane
   przez k6. `emptyDir` jest szybszy niż PV i nie wymaga provisionera storage.

4. **Custom RPS threshold = 100:** Ustalony eksperymentalnie. Przy 50 VU i fibonacci n=25,
   pojedynczy Pod obsługuje ~80–120 RPS zanim CPU osiągnie 100%. Próg 100 RPS/Pod
   zapewnia skalowanie zanim CPU stanie się wąskim gardłem.

5. **Strict abort-on-failure:** Każdy niezerowy exit code k6 przerywa cały `run-tests.sh all`.
   Zapewnia to, że nie marnujemy czasu na testy przy uszkodzonym klastrze, ale wymaga
   stabilnego środowiska.

6. **5 powtórzeń na scenariusz:** Kompromis między stabilnością statystyczną a czasem
   wykonania. Przy 8 scenariuszach każda dodatkowa repetycja to ~36 min.

### 11.3. Schemat Danych Wynikowych

```
results/
├── <scenario>/               # np. baseline-cpu, cpu-cpu, io-custom
│   ├── run-1/
│   │   ├── metadata.json     # scenario, service, hpa_strategy, timestamp, exit_code
│   │   └── <scenario>_run1_<timestamp>.json   # k6 summary-export
│   ├── run-2/
│   │   └── ...
│   └── ... (5 repetycji)
├── preflight/
│   └── preflight_<timestamp>.json
├── quick/
│   └── quick_<timestamp>.json
├── metrics_<timestamp>.txt   # Snapshot zapytań PromQL
└── summary.json              # Agregacja: mean, stddev, min, max dla każdego scenariusza
```

### 11.4. Stan HPA w Trakcie Testów

Skrypt [`run-tests.sh`](scripts/run-tests.sh) zarządza HPA per-scenariusz:

- **Przed scenariuszem:** `switch_hpa` usuwa wszystkie istniejące HPA dla danego serwisu,
  po czym nakłada plik odpowiadający wybranej strategii (`hpa-cpu.yaml`, `hpa-memory.yaml`,
  `hpa-custom.yaml`).
- **Cooldown (120s) między scenariuszami:** Skaluje oba serwisy do 1 repliki, usuwa
  wszystkie HPA, czeka 120s na stabilizację.
- **Baseline:** `strategy=none` — nie nakłada żadnego HPA, deployment zostaje z 1 repliką.

Metryki HPA są zbierane przez Prometheus (`kube_hpa_status_current_replicas`,
`kube_hpa_status_desired_replicas`) i wizualizowane w dashboardzie Grafana.

---

## 12. Poza Zakresem (Out of Scope)

Poniższe elementy NIE wchodzą w zakres niniejszego PRD i Fazy V–VII:

- **Multi-tenancy (Faza VIII):** Izolacja tenantów przez nagłówek `X-Tenant-ID` — osobny PRD
- **Memory-bound workload (Aplikacja C):** Usunięty z pierwszej iteracji; może być
  rozszerzeniem (`apps/memory-service/` istnieje jako szkielet)
- **Porównanie z VPA:** Vertical Pod Autoscaler vs HPA — dalszy kierunek badań
- **Service Mesh (Istio/Linkerd):** Wpływ na latency i time-to-scale — poza zakresem
- **KEDA (event-driven autoscaling):** Alternatywa dla prometheus-adapter — poza zakresem
- **Multi-node cluster:** Eksperymenty na jednym węźle; rozproszenie między węzłami
  nie jest badane
- **CI/CD pipeline:** Deployment ręczny przez skrypty; brak GitHub Actions/ArgoCD
- **Trwałość danych Prometheusa:** emptyDir, dane ulotne między restartami
- **Produkcyjne bezpieczeństwo:** CORS *, brak autentykacji, brak TLS między serwisami
- **Optymalizacja obrazów Docker:** `python:3.12-slim` bez multi-stage build

---

## 13. Uwagi Końcowe

### 13.1. Ryzyka

| Ryzyko | Prawdopodobieństwo | Wpływ | Mitigacja |
|---|---|---|---|
| OOM Kill przy 2 vCPU / 8 GB RAM | Średnie | Wysoki — testy przerwane | Użyć 4 vCPU / 16 GB RAM (rekomendowane) |
| Prometheus OOM przy dużym zapytaniu PromQL | Niskie | Średni — utrata metryk | Limit 1 Gi RAM; zapytania range-vector tylko dla okna testowego |
| k6 przekroczy progi (thresholds) | Średnie | Wysoki — abort testów | Zmniejszyć VU lub zwiększyć `maxReplicas`; rozważyć `--no-thresholds` |
| HPA nie zdąży przeskalować w 120s steady | Wysokie | Niskie — wyniki i tak wartościowe | Time-to-scale jest metryką badaną; wydłużenie fazy steady do 180s |
| Obrazy Docker nie zmieszczą się na 30 GB dysku | Niskie | Niski | 3 obrazy Python-slim to ~600 MB łącznie; 30 GB wystarcza |

### 13.2. Słownik Pojęć (Glosariusz)

| Termin | Definicja |
|---|---|
| **Workload (Obciążenie)** | Charakterystyka obciążeniowa aplikacji determinująca wąskie gardło |
| **CPU-bound** | Obciążenie ograniczone przez CPU (>80% użycia, RAM stabilne, I/O wait zerowy) |
| **I/O-bound** | Obciążenie ograniczone przez czas oczekiwania I/O (CPU <15%, I/O wait dominujący) |
| **HPA** | HorizontalPodAutoscaler — mechanizm automatycznego skalowania liczby Pod-ów |
| **Strategia HPA** | Metoda podejmowania decyzji o skalowaniu (Resource CPU, Resource Memory, Custom RPS) |
| **Scenariusz** | Kombinacja workloadu + strategii HPA + profilu obciążenia k6 |
| **Powtórzenie** | Pojedyncze uruchomienie testu k6 w ramach scenariusza (5 na scenariusz) |
| **Cooldown** | Okres stabilizacji klastra między scenariuszami (120s) |
| **Preflight** | Test weryfikacyjny przed pełnym eksperymentem |
| **Time-to-scale** | Czas od przekroczenia progu HPA do gotowości nowego Pod-a |
| **Efektywność skalowania** | Throughput na replikę (RPS / liczba Pod-ów) |

### 13.3. Referencje do Kodu

| Komponent | Ścieżka |
|---|---|
| CPU-service (kod) | [`apps/cpu-service/main.py`](apps/cpu-service/main.py) |
| IO-service (kod) | [`apps/io-service/main.py`](apps/io-service/main.py) |
| Echo-server (kod) | [`apps/echo-server/main.py`](apps/echo-server/main.py) |
| Deployment CPU | [`k8s/cpu-service/deployment.yaml`](k8s/cpu-service/deployment.yaml) |
| HPA CPU (50%) | [`k8s/cpu-service/hpa-cpu.yaml`](k8s/cpu-service/hpa-cpu.yaml) |
| HPA Memory (70%) | [`k8s/cpu-service/hpa-memory.yaml`](k8s/cpu-service/hpa-memory.yaml) |
| HPA Custom (RPS) | [`k8s/cpu-service/hpa-custom.yaml`](k8s/cpu-service/hpa-custom.yaml) |
| Deployment IO | [`k8s/io-service/deployment.yaml`](k8s/io-service/deployment.yaml) |
| HPA IO CPU | [`k8s/io-service/hpa-cpu.yaml`](k8s/io-service/hpa-cpu.yaml) |
| Prometheus | [`k8s/monitoring/prometheus.yaml`](k8s/monitoring/prometheus.yaml) |
| Grafana | [`k8s/monitoring/grafana.yaml`](k8s/monitoring/grafana.yaml) |
| Prometheus Adapter | [`k8s/monitoring/prometheus-adapter.yaml`](k8s/monitoring/prometheus-adapter.yaml) |
| Adapter Config | [`k8s/monitoring/prometheus-adapter-config.yaml`](k8s/monitoring/prometheus-adapter-config.yaml) |
| Dashboard Grafana | [`monitoring/dashboards/overview-dashboard.json`](monitoring/dashboards/overview-dashboard.json) |
| k6 CPU test | [`tests/k6/cpu-load-test.js`](tests/k6/cpu-load-test.js) |
| k6 IO test | [`tests/k6/io-load-test.js`](tests/k6/io-load-test.js) |
| k6 Combined test | [`tests/k6/combined-scenario.js`](tests/k6/combined-scenario.js) |
| Skrypt deploy | [`scripts/deploy-k3s.sh`](scripts/deploy-k3s.sh) |
| Skrypt testowy | [`scripts/run-tests.sh`](scripts/run-tests.sh) |
| Wrapper | [`scripts/run-all.sh`](scripts/run-all.sh) |
| Kontekst (Faza V) | [`CONTEXT.md`](CONTEXT.md) |
| Plan pracy | [`plan_pracy_magisterskiej_v2.md`](plan_pracy_magisterskiej_v2.md) |
