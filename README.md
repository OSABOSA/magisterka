# System skalowalny w modelu usługowym — praca magisterska

Projekt składa się z 2 aplikacji FastAPI reprezentujących różne profile obciążeniowe (CPU-bound, I/O-bound), wdrożonych lokalnie z Docker Compose oraz na klastrze Kubernetes, z pełnym monitoringiem (Prometheus + Grafana), custom metrics HPA (prometheus-adapter) i testami obciążeniowymi (k6).

## Oś badawcza

**Macierz 2×3:** CPU-bound, I/O-bound × HPA CPU, HPA Memory, HPA Custom (RPS) — 8 scenariuszy.

Zobacz [`plan_pracy_magisterskiej_v2.md`](plan_pracy_magisterskiej_v2.md) po pełną specyfikację.

## Struktura katalogów

```
magisterka/
├── .env                          # Zmienne środowiskowe (DOMAIN, TAIL_ID)
├── docker-compose.yaml           # Lokalne środowisko deweloperskie (2 serwisy FastAPI + Traefik + tsdproxy)
├── README.md                     # Ten plik
├── plan_pracy_magisterskiej_v2.md # Plan pracy magisterskiej
├── apps/                         # Kod źródłowy aplikacji FastAPI
│   ├── cpu-service/              # CPU-bound: przetwarzanie obrazów, Fibonacci
│   └── io-service/               # I/O-bound: symulowane opóźnienia
├── k8s/                          # Manifesty Kubernetes
│   ├── base/                     # Namespace
│   ├── cpu-service/              # Deployment, Service, hpa-cpu.yaml, hpa-memory.yaml, hpa-custom.yaml
│   ├── io-service/               # Deployment, Service, hpa-cpu.yaml, hpa-memory.yaml, hpa-custom.yaml
│   └── monitoring/               # Prometheus, Grafana, prometheus-adapter
├── tests/
│   └── k6/                       # Skrypty testów obciążeniowych k6 (cpu, io, combined)
├── monitoring/
│   └── dashboards/               # Dashboardy Grafana
└── scripts/
    ├── deploy-k3s.sh             # Deployment na klaster k3s
    └── run-tests.sh             # Automatyzacja badań (macierz 2×3, 8 scenariuszy)
```

## Szybki start (development)

```bash
# Sklonuj repozytorium i przejdź do katalogu
cd magisterka

# Uruchom oba serwisy
docker compose up -d

# Serwisy dostępne pod:
#   cpu-service     → cpu.magisterka.osabosa.pl
#   io-service      → io.magisterka.osabosa.pl
```

## Wymagania

- **Docker** ≥ 24.x
- **Docker Compose** ≥ v2.x
- **kubectl** (do wdrożeń K8s)
- **k3s** lub **kind** (lokalny klaster Kubernetes)
- **k6** (testy obciążeniowe)
