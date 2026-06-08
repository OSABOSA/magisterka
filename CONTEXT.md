# Context

## Workload (Obciążenie)

Charakterystyka obciążeniowa aplikacji determinująca, które zasoby są wąskim gardłem.

### CPU-bound Workload

Obciążenie, którego wąskim gardłem jest procesor. CPU przy obciążeniu wysokie (~80–100%), RAM stabilne i niskie, I/O wait zerowy. Przetwarzanie obrazów, obliczenia numeryczne (Fibonacci).

### I/O-bound Workload

Obciążenie, którego wąskim gardłem jest czas oczekiwania na operacje wejścia/wyjścia. CPU przy obciążeniu niskie (~5–15%), RAM stabilne i niskie, I/O wait dominujący. Łańcuchy zapytań do zewnętrznych usług, asynchroniczne oczekiwanie.

## HPA Strategy (Strategia HPA)

Metoda podejmowania decyzji o skalowaniu przez Kubernetes HorizontalPodAutoscaler na podstawie określonej metryki.

### HPA CPU (Resource Metric)

Strategia skalowania na podstawie średniego zużycia CPU wszystkich Podów. Próg: 50%. Oczekiwana skuteczność: ✅ CPU-bound / ❌ I/O-bound.

### HPA Memory (Resource Metric)

Strategia skalowania na podstawie średniego zużycia RAM wszystkich Podów. Próg: 70%. Oczekiwana skuteczność: ❌ CPU-bound / ❌ I/O-bound.

### HPA Custom (RPS przez prometheus-adapter)

Strategia skalowania na podstawie liczby żądań HTTP na sekundę na Pod. Próg: 100 RPS/Pod. Oczekiwana skuteczność: ✅ CPU-bound / ✅ I/O-bound.

## Research Metrics (Metryki Badawcze)

### Latency

Czas odpowiedzi — percentyle 50, 95, 99. Jednostka: ms. Źródło: k6 + Prometheus.

### Throughput

Łączna liczba obsłużonych żądań na sekundę. Jednostka: RPS. Źródło: k6 + Prometheus.

### Time-to-scale

Czas od przekroczenia progu HPA do gotowości nowego Pod-a. Jednostka: s. Źródło: Prometheus + metryki K8s.

### Scaling Efficiency (Efektywność skalowania)

Throughput na replikę (RPS / liczba Pod-ów). Jednostka: RPS/Pod. Źródło: obliczeniowa.

### Error Rate

Odsetek żądań zakończonych błędem (4xx, 5xx, timeout). Jednostka: %. Źródło: k6.

## Scenario (Scenariusz)

Pojedyncza kombinacja workloadu i strategii HPA, wraz z przypisanym profilem obciążenia k6.

### Baseline Scenario

Scenariusz referencyjny bez HPA. Dwa warianty: 1-replika (baseline) i 3-repliki (static overprovisioning baseline).

## Experiment (Eksperyment)

Pełne uruchomienie wszystkich scenariuszy. Obejmuje 10 scenariuszy (2 workloady × 3 strategie HPA + 2 baseline 1-rep + 2 baseline 3-rep), każdy z 5 powtórzeniami, z 2-minutowym cooldownem między scenariuszami.

## Repetition (Powtórzenie)

Pojedyncze uruchomienie testu k6 w ramach scenariusza. Każdy scenariusz ma 5 powtórzeń dla stabilności statystycznej.

## Cooldown

Okres stabilizacji klastra między scenariuszami (120s). Oba serwisy skalowane do 1 repliki, wszystkie HPA usuwane.

## Preflight Test

Test weryfikacyjny (~5 min) uruchamiany przed pełnym eksperymentem w celu potwierdzenia, że HPA poprawnie wykrywa obciążenie i skaluje deployment.

---

## Faza V Decisions (2026-05-24)

1. **Grafana dashboards**: One overview dashboard. Grafana screenshots for thesis. Auto-launch Grafana port-forward during `run-tests.sh all`.
2. **Prometheus metrics**: Range-vector queries over test time window. k6 start/end timestamps passed to Prometheus queries.
3. **Scenarios**: 10 total (added `baseline-cpu-3rep` and `baseline-io-3rep` — static 3-replica, no HPA). Custom RPS threshold stays at 100.
4. **Failure handling**: Any non-zero k6 exit code aborts the entire `run-tests.sh all` immediately.
5. **Summary output**: Generate `results/summary.json` (aggregated: mean, stddev, min, max across 5 reps) + `results/summary.csv`.

## Faza VI Decisions (2026-05-24)

1. **Thesis structure**: Create `magisterka/thesis/` as sibling to `template/`. Copy `mgr.cls`, build `main.tex` fresh. Template stays pristine.
