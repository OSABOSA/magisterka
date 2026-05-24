# Plan Pracy Magisterskiej (v2)

> **Temat:** Projekt i analiza porównawcza wydajności skalowalnych architektur systemów typu SaaS
>
> **Oś badawcza (podstawowa):** Macierz 2×3 — 2 typy workloadu × 3 strategie HPA
>
> **Hipoteza:** Nie ma jednej uniwersalnej metryki HPA — skuteczność strategii skalowania zależy od charakterystyki obciążeniowej aplikacji. Błędny wybór metryki prowadzi do niedoskalowania lub przeskalowania.

---

## Oficjalne Założenia Pracy (z karty przedmiotu)

**Cel i zakres:**
Celem pracy jest zaprojektowanie i implementacja prototypu skalowalnego systemu oprogramowania realizowanego w modelu usługowym oraz przeprowadzenie analizy parametrów wydajnościowych wybranych rozwiązań architektonicznych. Zakres pracy obejmuje analizę teoretyczną zagadnień związanych z systemami rozproszonymi, budowę autorskiego rozwiązania systemowego w wybranej technologii oraz przeprowadzenie badań eksperymentalnych weryfikujących efektywność zastosowanych technik skalowania i optymalizacji w środowisku sieciowym.

**Zadania do wykonania:**
1. Analiza literatury przedmiotu i aktualnych trendów w zakresie budowy systemów skalowalnych oraz architektur chmurowych.
2. Opracowanie założeń projektowych oraz specyfikacji technicznej prototypu systemu.
3. Implementacja systemu oprogramowania z wykorzystaniem wybranych narzędzi i platform programistycznych.
4. Przygotowanie środowiska testowego umożliwiającego monitorowanie oraz pomiar kluczowych wskaźników wydajnościowych.
5. Przeprowadzenie serii badań eksperymentalnych weryfikujących zachowanie systemu pod zmiennym obciążeniem.
6. Opracowanie i interpretacja wyników badań, analiza porównawcza wybranych scenariuszy oraz sformułowanie wniosków końcowych.

---

## Stos Technologiczny (rekomendowany)

| Warstwa | Technologia | Uzasadnienie |
|---|---|---|
| Język / Framework | Python 3.12 + FastAPI | Ekosystem znajomy autorowi; natywne async, automatyczny OpenAPI |
| Konteneryzacja | Docker + Docker Compose | Do lokalnego developmentu |
| Orkiestracja | Kubernetes (k3s) | HPA, service discovery, rolling updates — główny obiekt badań |
| Orkiestracja (chmura) | GCP GKE | Managed Kubernetes — porównanie lokal vs chmura (Faza VII) |
| Monitoring | Prometheus + Grafana | Zbieranie metryk z K8s i aplikacji; dashboardy do analizy |
| Custom Metrics HPA | prometheus-adapter | Wystawia metryki aplikacyjne (RPS) jako metryki HPA w K8s |
| Testy obciążeniowe | k6 (Grafana k6) | Skryptowalne w JS, generuje metryki kompatybilne z Prometheusem |

---

## Dwie Aplikacje — Charakterystyki Obciążeniowe

### Aplikacja A: CPU-bound — Image Processing Service

| Aspekt | Opis |
|---|---|
| **Co robi** | Przyjmuje obrazek, wykonuje operacje CPU-intensywne (zmiana rozmiaru, filtr graficzny, analiza histogramu), zwraca statystyki |
| **Endpoint(y)** | `POST /process` (przetwarzanie obrazu), `GET /fibonacci?n=X` (czyste CPU, O(2^n)) |
| **Wąskie gardło** | Procesor — każde żądanie w pełni obciąża CPU przez setki ms |
| **CPU przy obciążeniu** | Wysokie (~80–100%) |
| **RAM przy obciążeniu** | Stabilne, niskie |
| **I/O wait** | Zerowy |

### Aplikacja B: I/O-bound — Data Query Service

| Aspekt | Opis |
|---|---|
| **Co robi** | Symuluje łańcuch zapytań do zewnętrznych usług poprzez `asyncio.sleep()` z konfigurowalnym opóźnieniem i losowym jitterem |
| **Endpoint(y)** | `GET /query?delay=X&steps=N` (N kroków, każdy po X ms ±20% jitter) |
| **Wąskie gardło** | Czas oczekiwania (I/O wait) — serwer "wisi" czekając, CPU prawie bezczynne |
| **CPU przy obciążeniu** | Niskie (~5–15%) |
| **RAM przy obciążeniu** | Stabilne, niskie |
| **I/O wait** | Dominujący — setki równoczesnych oczekujących korutyn |

> **Uwaga:** Memory-bound (Aplikacja C) został usunięty z pierwszej iteracji. Może zostać dodany jako rozszerzenie w dalszych kierunkach badań.
---

## Trzy Strategie HPA — Definicje

### Strategia 1: HPA na CPU (Resource Metric)

| Aspekt | Opis |
|---|---|
| **Co mierzy** | Średnie zużycie CPU wszystkich Podów (`averageUtilization`) |
| **Próg** | 50% |
| **Sygnał do skalowania** | CPU > 50% → dodaj Pod; CPU < 50% → usuń Pod |
| **Oczekiwana skuteczność** | ✅ CPU-bound / ❌ I/O-bound |

### Strategia 2: HPA na Memory (Resource Metric)

| Aspekt | Opis |
|---|---|
| **Co mierzy** | Średnie zużycie RAM wszystkich Podów (`averageUtilization`) |
| **Próg** | 70% |
| **Sygnał do skalowania** | RAM > 70% → dodaj Pod; RAM < 70% → usuń Pod |
| **Oczekiwana skuteczność** | ❌ CPU-bound / ❌ I/O-bound |

### Strategia 3: HPA na Custom Metric (RPS przez prometheus-adapter)

| Aspekt | Opis |
|---|---|
| **Co mierzy** | Liczba żądań HTTP na sekundę na Pod (`http_requests_per_second`) |
| **Próg** | Do ustalenia eksperymentalnie (np. 100 RPS/Pod) |
| **Źródło metryki** | Prometheus scrapuje `/metrics` z FastAPI → prometheus-adapter wystawia jako metrykę K8s |
| **Oczekiwana skuteczność** | ✅ CPU-bound / ✅ I/O-bound |

---

## Macierz Badawcza 2×3

|  | HPA CPU | HPA Memory | HPA Custom (RPS) |
|---|---|---|---|
| **CPU-bound** | ✅ Optymalna | ❌ Nie widzi przeciążenia | ⚠️ Działa, ale wolniejsza reakcja |
| **I/O-bound** | ❌ Nie widzi przeciążenia | ❌ Nie widzi przeciążenia | ✅ Optymalna |

> ✅ = powinna skutecznie wykryć przeciążenie i przeskalować
> ❌ = nie wykryje przeciążenia → niedoskalowanie
> ⚠️ = wykryje, ale z opóźnieniem lub nieoptymalnie

---

## Metryki Badawcze — Co Mierzymy

| Metryka | Definicja | Jednostka | Źródło |
|---|---|---|---|
| **Latency (p50, p95, p99)** | Czas odpowiedzi — percentyle 50, 95, 99 | ms | k6 + Prometheus |
| **Throughput** | Łączna liczba obsłużonych żądań na sekundę | RPS | k6 + Prometheus |
| **Time-to-scale** | Czas od przekroczenia progu HPA do gotowości nowego Pod-a | s | Prometheus + metryki K8s |
| **Efektywność skalowania** | Throughput na replikę (RPS / liczba Pod-ów) | RPS/Pod | Obliczeniowa |
| **Error rate** | Odsetek żądań zakończonych błędem (4xx, 5xx, timeout) | % | k6 |

---

## Fazy Realizacji

### Faza 0: Inicjalizacja — jedna aplikacja, jedna strategia (minimalny setup)

**Cel:** Postawić najprostszy działający system — CPU-service z HPA CPU — i zebrać pierwsze wyniki.

| Krok | Opis |
|---|---|
| 0.1 | Repozytorium Git, struktura katalogów |
| 0.2 | Aplikacja A (CPU-service) — FastAPI + Prometheus metrics + Dockerfile |
| 0.3 | Lokalny klaster Kubernetes (k3s) |
| 0.4 | Manifesty K8s: Deployment, Service, HPA (CPU, target 50%) |
| 0.5 | Prometheus + Grafana — podstawowy monitoring |
| 0.6 | Skrypt testowy k6 dla CPU-service |
| 0.7 | Pierwszy eksperyment: baseline (1 replika) vs HPA CPU |
| 0.8 | Zapisanie wyników, wstępna analiza |

**Efekt:** Działający, najmniejszy możliwy system badawczy. Można odpalić test i dostać wyniki.

### Faza I: Analiza Literatury i Podstawy Teoretyczne (rozdziały 1–2)

**Cel:** Zbudować fundament teoretyczny pracy. **Równolegle z Fazą 0.**

| Krok | Opis |
|---|---|
| I.1 | Przegląd literatury: skalowalność pozioma/pionowa, modele usługowe (SaaS/IaaS/PaaS), konteneryzacja, orkiestracja |
| I.2 | Systematyka pojęć: skalowalność, HPA, metryki skalowania, workload profile |
| I.3 | Przegląd narzędzi: K8s vs Docker Swarm vs Nomad; Prometheus vs Datadog vs ELK; k6 vs Locust vs JMeter |
| I.4 | Stan badań: istniejące badania nad HPA w K8s, luki badawcze |
| I.5 | Tekst rozdziałów 1–2 |

**Efekt:** ~20–30 stron tekstu.

### Faza II: Projekt Architektury i Specyfikacja (rozdział 3)

**Cel:** Zdefiniować co dokładnie budujemy i jak to zmierzymy.

| Krok | Opis |
|---|---|
| II.1 | Diagram architektury: Ingress → Services → Pods → Monitoring |
| II.2 | Specyfikacja API każdej aplikacji (OpenAPI z FastAPI) |
| II.3 | Opis trzech strategii HPA — mechanizm, metryki, progi |
| II.4 | Metryki badawcze — definicje operacyjne |
| II.5 | Plan eksperymentów: 6 scenariuszy (macierz 2×3), zmienne niezależne/zależne |
| II.6 | Tekst rozdziału 3 |

**Efekt:** Rozdział 3 + konkretny plan implementacji.

### Faza III: Implementacja Pełnego Prototypu (rozdział 4)

**Cel:** Zbudować dwie aplikacje, trzy warianty HPA dla każdej.

| Krok | Opis |
|---|---|
| III.1 | Aplikacja A (CPU-service): ukończona w Fazie 0, ew. poprawki |
| III.2 | Aplikacja B (IO-service): `GET /query` z `asyncio.sleep`, Dockerfile |
| III.3 | HPA CPU dla obu aplikacji |
| III.4 | HPA Memory dla obu aplikacji |
| III.5 | prometheus-adapter + HPA Custom (RPS) dla obu aplikacji |
| III.6 | Dashboard Grafana — metryki biznesowe + infrastrukturalne |
| III.7 | Testy jednostkowe (pytest) |
| III.8 | Tekst rozdziału 4 |

**Efekt:** Kompletny prototyp + rozdział 4.

### Faza IV: Przygotowanie Środowiska Testowego

**Cel:** Wszystko skonfigurowane, jedno polecenie odpala test.

| Krok | Opis |
|---|---|
| IV.1 | Klaster k3s — instalacja, konfiguracja, weryfikacja |
| IV.2 | Deployment wszystkich komponentów (2 aplikacje + monitoring) |
| IV.3 | Prometheus + Grafana — konfiguracja scraping, dashboardy |
| IV.4 | Skrypty k6 dla każdej aplikacji: stałe obciążenie, ramp-up, burst |
| IV.5 | Skrypt automatyzujący: `run-tests.sh` — odpala testy, zbiera wyniki, przełącza HPA między scenariuszami |
| IV.6 | Weryfikacja: test dymny (wszystkie endpointy, wszystkie metryki zbierane) |

**Efekt:** Środowisko gotowe do badań. Jedno polecenie = komplet wyników.

### Faza V: Badania Eksperymentalne (rozdział 5)

**Cel:** Przeprowadzić 6 scenariuszy (macierz 2×3) na k3s, zebrać dane.

| Krok | Scenariusz | Aplikacja | Strategia HPA |
|---|---|---|---|
| V.1 | Baseline (bez HPA, 1 replika) | A (CPU-bound) | — |
| V.2 | Baseline (bez HPA, 1 replika) | B (I/O-bound) | — |
| V.3 | HPA CPU | A (CPU-bound) | CPU |
| V.4 | HPA CPU | B (I/O-bound) | CPU |
| V.5 | HPA Memory | A (CPU-bound) | Memory |
| V.6 | HPA Memory | B (I/O-bound) | Memory |
| V.7 | HPA Custom (RPS) | A (CPU-bound) | Custom |
| V.8 | HPA Custom (RPS) | B (I/O-bound) | Custom |

Każdy scenariusz:
- Ten sam profil obciążenia (ramp-up → steady → ramp-down)
- Pomiar: latency (p50/p95/p99), throughput, time-to-scale, efektywność skalowania, error rate
- Zapis surowych danych do CSV/JSON
- Przed każdym scenariuszem: kubectl apply właściwego HPA, kubectl delete poprzedniego

**Efekt:** Kompletny zbiór danych pomiarowych (8 scenariuszy) + tekst rozdziału 5.

### Faza VI: Opracowanie Wyników, Analiza i Wnioski (rozdziały 5–6)

| Krok | Opis |
|---|---|
| VI.1 | Wizualizacja wyników — wykresy porównawcze (macierz 2×3) |
| VI.2 | Analiza statystyczna — średnie, percentyle, odchylenia, korelacje |
| VI.3 | Interpretacja — która strategia dla którego workloadu i dlaczego |
| VI.4 | Tabela decyzyjna: "dla aplikacji typu X wybierz strategię Y" |
| VI.5 | Wnioski końcowe — rekomendacje, ograniczenia badań, kierunki dalszych prac |
| VI.6 | Finalizacja — streszczenie PL+EN, spis treści/rysunków/tabel, bibliografia |

**Efekt:** Gotowa praca magisterska (część rdzeniowa).

---

### Faza VII: Rozszerzenie — Wdrożenie w Chmurze (rozdział 5, sekcja dodatkowa)

**Cel:** Porównać zachowanie systemu w środowisku chmurowym (managed Kubernetes) vs lokalnym (k3s). **Wymóg formalny** — "architektury chmurowe" w temacie pracy.

| Krok | Opis |
|---|---|
| VII.1 | Wybór dostawcy (GCP GKE — $300 kredytów na start) i utworzenie klastra (2–3 nody) |
| VII.2 | Deployment systemu na GKE (2 aplikacje + monitoring) |
| VII.3 | Powtórzenie wybranych scenariuszy z Fazy V w chmurze |
| VII.4 | Porównanie: latency, time-to-scale, throughput — lokalny k3s vs GKE |
| VII.5 | Tekst: sekcja "Środowisko chmurowe" w rozdziale 5 |

**Efekt:** Dane porównawcze lokalny-vs-chmura + podsekcja w rozdziale 5.

### Faza VIII: Rozszerzenie — Model SaaS (Multi-Tenancy) (rozdział 4, sekcja dodatkowa)

**Cel:** Dodać wielodostępność (multi-tenancy) do aplikacji — kluczowy aspekt "modelu usługowego (SaaS)". **Wymóg formalny** — "system realizowany w modelu usługowym" w temacie pracy. Każdy tenant ma izolowane dane, współdzieli tę samą infrastrukturę.

| Krok | Opis |
|---|---|
| VIII.1 | Projekt modelu tenantów — nagłówek `X-Tenant-ID`, izolacja danych per tenant |
| VIII.2 | Aplikacja A (CPU-service): tenanty jako metadane — pomiar per tenant nie zmienia logiki CPU |
| VIII.3 | Aplikacja B (IO-service): tenanty z izolowanymi kolejkami opóźnień |
| VIII.4 | Testy: wielu tenantów współdzieli system, obciążenie jednego nie wpływa na drugiego |
| VIII.5 | Tekst: sekcja "Model SaaS — wielodostępność" w rozdziale 4 |

**Efekt:** System w pełni zgodny z modelem SaaS + podsekcja w rozdziale 4.

---

## Harmonogram (Pełny)

```
Faza 0   [====]     Minimalny setup (CPU-service + HPA CPU)
Faza I   [========]  Analiza literatury (równolegle z Fazą 0)
Faza II  [====]      Projekt architektury
Faza III [======]    Implementacja pełnego prototypu (2 aplikacje, 3×HPA)
Faza IV  [====]      Środowisko testowe
Faza V   [======]    Badania eksperymentalne — macierz 2×3 na k3s (8 scenariuszy)
Faza VI  [======]    Opracowanie wyników + redakcja
Faza VII [===]       Rozszerzenie: Chmura (GKE) — OBOWIĄZKOWE
Faza VIII [===]       Rozszerzenie: SaaS (Multi-Tenancy) — OBOWIĄZKOWE
```

Fazy VII i VIII są **obowiązkowe** (wymóg formalny z karty przedmiotu). Można je realizować w dowolnej kolejności po Fazie V.

---

## Ryzyka i Mitigacje

| Ryzyko | Mitigacja |
|---|---|
| Za mało RAM na k3s (QNAP 8 GB) | k3s jest lekki; ograniczyć monitoring do minimum; testy obciążeniowe generują ruch, nie przechowują danych |
| Krzywa uczenia się Kubernetes | Zacząć od fazy 0 (minimalny setup), stopniowo dokładać komponenty |
| Prometheus-adapter trudny w konfiguracji | Zostawić na koniec Fazy III; HPA CPU i Memory nie wymagają adaptera |
| Za mało czasu na tekst | Pisać równolegle z implementacją — każda faza produkuje fragment tekstu |
| Problemy z odtworzeniem wyników | Wszystko w Git; skrypt automatyzujący; wersjonowanie obrazów Docker |
| Koszty chmury (GKE) | GCP $300 kredytów na 90 dni; wyłączać klaster po testach; najmniejszy możliwy klaster |
| Zbyt dużo faz — nie starczy czasu | Priorytet: Fazy 0–VI (rdzeń) → VII (chmura) → VIII (SaaS). Każda faza produkuje kompletny, samodzielny wynik |
| Brakujące pliki HPA (Memory, Custom) | Do utworzenia w Fazie III; organizacja: osobne pliki `hpa-cpu.yaml`, `hpa-memory.yaml`, `hpa-custom.yaml` per serwis |

---

## Struktura Pracy

1. **Wstęp** — cel, zakres, problem badawczy, hipoteza
2. **Analiza literatury i podstawy teoretyczne** — systemy rozproszone, skalowalność, konteneryzacja, Kubernetes, HPA, monitoring, architektury chmurowe, model SaaS
3. **Projekt i specyfikacja techniczna** — architektura, API, strategie HPA, metryki, plan eksperymentów
4. **Implementacja prototypu** — stos technologiczny, struktura kodu, aplikacje, konfiguracje HPA, monitoring, model SaaS (multi-tenancy)
5. **Badania eksperymentalne i analiza wyników** — środowisko (lokalne k3s + chmurowe GKE), scenariusze (macierz 2×3), wyniki, interpretacja, porównanie lokal vs chmura
6. **Podsumowanie i wnioski** — tabela decyzyjna, rekomendacje, ograniczenia, dalsze kierunki
7. **Bibliografia**
8. **Załączniki** — kod źródłowy, konfiguracje K8s, surowe dane pomiarowe

---

## Dalsze Kierunki Badań (poza zakresem pracy)

- Memory-bound workload jako trzeci wymiar macierzy (3×3 zamiast 2×3)
- Porównanie VPA vs HPA
- Wpływ Service Mesh (Istio) na czas skalowania i latency
- HPA z KEDA (event-driven autoscaling) zamiast prometheus-adapter
- Skalowanie Redis (Redis Cluster, sentinel) przy ekstremalnym obciążeniu
