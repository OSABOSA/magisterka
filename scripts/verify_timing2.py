#!/usr/bin/env python3
"""
Deep-dive: understand where the current ∼150s and ∼135s values come from.
Compare: test start (from k6), threshold crossing (from CPU CSV), pod creation (from replicas CSV).
"""
import os, sys, csv, re
from datetime import datetime
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent.parent / "tests" / "results"

def read_utf16le_tsv(path):
    with open(path, 'rb') as f:
        raw = f.read()
    text = raw.decode('utf-16-le')
    lines = text.strip().split('\n')
    if not lines:
        return [], []
    reader = csv.reader(lines, delimiter='\t')
    rows = list(reader)
    if len(rows) < 2:
        return [], []
    return rows[0], rows[1:]

def parse_replicas_csv(path):
    header, rows = read_utf16le_tsv(path)
    timestamps, replicas = [], []
    for row in rows:
        if len(row) < 2:
            continue
        try:
            ts = datetime.strptime(row[0].strip(), "%Y-%m-%d %H:%M:%S")
            r = float(row[1].strip())
            timestamps.append(ts)
            replicas.append(r)
        except (ValueError, IndexError):
            continue
    return timestamps, replicas

def find_csv(test_dir, pattern):
    for f in sorted(test_dir.iterdir()):
        if f.suffix == '.csv' and pattern.lower() in f.name.lower():
            return f
    return None

# ── Helpers ──
def find_first_scale_event(ts_rep, reps):
    """Find first timestamp where replicas exceed minReplicas (2)."""
    for i in range(1, len(ts_rep)):
        if reps[i] > 2.5:  # minReplicas=2, so first scale-up beyond 2
            return ts_rep[i], reps[i]
    return None, None

def find_peak_ts(ts_rep, reps):
    max_r = max(reps)
    for i in range(len(ts_rep)):
        if reps[i] == max_r:
            return ts_rep[i], max_r
    return None, None

def find_scale_start(ts_rep, reps):
    """Find when replicas first increased from the initial value."""
    initial = reps[0]
    for i in range(1, len(ts_rep)):
        if reps[i] > initial + 0.5:
            return ts_rep[i], initial, reps[i]
    return None, initial, initial

def find_cpu_threshold_crossing(ts_cpu, pod_data, threshold_mcpu):
    """Find first timestamp where avg CPU across active pods exceeds threshold."""
    for i in range(len(ts_cpu)):
        pod_vals = [pod_data[j][i] for j in range(len(pod_data)) 
                     if i < len(pod_data[j]) and pod_data[j][i] > 0.0001]
        if not pod_vals:
            continue
        avg_mcpu = sum(pod_vals) / len(pod_vals) * 1000
        if avg_mcpu > threshold_mcpu:
            return ts_cpu[i], avg_mcpu, len(pod_vals)
    return None, None, None

def parse_k6_timestamp(path):
    """Try to extract when k6 started from the report."""
    with open(path, 'r') as f:
        text = f.read()
    # Look for execution time info
    m = re.search(r'execution:\s*local', text)
    # Try to find timestamps in the JSON summary (if it exists)
    json_path = path.parent / path.name.replace('raport_', 'summary_').replace('.txt', '.json')
    if json_path.exists():
        import json
        with open(json_path) as jf:
            jd = json.load(jf)
        if 'state' in jd:
            return jd['state'].get('testRunDurationMs', None)
    return None

# ── Main analysis ──

def analyze_test(test_name, threshold_mcpu, service_label):
    test_dir = RESULTS_DIR / test_name
    
    cpu_csv = find_csv(test_dir, f"Zużycie CPU przez {service_label}")
    replicas_csv = find_csv(test_dir, "Liczba aktywnych replik")
    report_files = list(test_dir.glob("raport_*.txt"))
    
    if not cpu_csv or not replicas_csv:
        print(f"  Missing CSVs")
        return
    
    ts_cpu, pod_data, pod_names = read_utf16le_tsv(cpu_csv) if False else __import__('analyze_results_standalone')  
    # Re-parse properly
    _, _ = read_utf16le_tsv(cpu_csv)  # skip
    
    ts_rep, reps = parse_replicas_csv(replicas_csv)
    
    # Parse CPU properly
    import analyze_results as ar
    ts_cpu, pod_data, pod_names = ar.parse_cpu_csv(cpu_csv)
    
    # Parse k6 report
    k6_data = {}
    if report_files:
        k6_data = ar.parse_k6_report(report_files[0])
    
    print(f"\n{'='*60}")
    print(f"TEST: {test_name} (threshold={threshold_mcpu}m)")
    print(f"{'='*60}")
    
    # Key timestamps
    print(f"\n  Key timestamps:")
    print(f"    Replicas data: {ts_rep[0]} → {ts_rep[-1]} ({len(ts_rep)} pts)")
    print(f"    CPU data:      {ts_cpu[0]} → {ts_cpu[-1]} ({len(ts_cpu)} pts)")
    
    # Find scaling events
    scale_start_ts, initial_r, new_r = find_scale_start(ts_rep, reps)
    peak_ts, peak_r = find_peak_ts(ts_rep, reps)
    threshold_ts, threshold_avg, threshold_n = find_cpu_threshold_crossing(ts_cpu, pod_data, threshold_mcpu)
    
    print(f"\n  Events:")
    print(f"    Scale start:   {scale_start_ts} ({initial_r:.0f}→{new_r:.0f})" if scale_start_ts else "    Scale start: N/A")
    print(f"    Peak replicas: {peak_ts} ({peak_r:.0f})" if peak_ts else "    Peak: N/A")
    print(f"    CPU threshold: {threshold_ts} (avg={threshold_avg:.0f}m, n={threshold_n})" if threshold_ts else "    CPU threshold: N/A")
    
    # Compute various timing interpretations
    print(f"\n  Possible time-to-scale interpretations:")
    
    if scale_start_ts:
        first_sample = ts_rep[0]
        delta_from_first = (scale_start_ts - first_sample).total_seconds()
        print(f"    A) First replica sample → First scale event: {delta_from_first:.0f}s")
        
        if peak_ts:
            delta_scale_to_peak = (peak_ts - scale_start_ts).total_seconds()
            print(f"    B) First scale event → Peak replicas: {delta_scale_to_peak:.0f}s")
    
    if threshold_ts and scale_start_ts:
        delta = (scale_start_ts - threshold_ts).total_seconds()
        print(f"    C) CPU threshold → First scale event: {delta:.0f}s {'(NEGATIVE — scale before threshold!)' if delta < 0 else ''}")
    
    if threshold_ts and peak_ts:
        delta = (peak_ts - threshold_ts).total_seconds()
        print(f"    D) CPU threshold → Peak replicas: {delta:.0f}s {'(NEGATIVE!)' if delta < 0 else ''}")
    
    if scale_start_ts and peak_ts:
        delta = (peak_ts - scale_start_ts).total_seconds()
        print(f"    E) Scale start → Peak: {delta:.0f}s")
    
    # Also: time from first CPU data point showing >0 to peak
    first_cpu_ts = ts_cpu[0]
    first_meaningful = None
    for i in range(len(ts_cpu)):
        vals = [pod_data[j][i] for j in range(len(pod_data)) if i < len(pod_data[j]) and pod_data[j][i] > 0.01]
        if vals:
            first_meaningful = ts_cpu[i]
            break
    
    if first_meaningful and peak_ts:
        delta = (peak_ts - first_meaningful).total_seconds()
        print(f"    F) First CPU data → Peak: {delta:.0f}s (first CPU: {first_meaningful})")
    
    # k6 test duration from report
    if k6_data.get('duration_s'):
        print(f"\n  k6 test duration: {k6_data['duration_s']:.0f}s")
    
    # What if "time to scale" = time from test start (replica data start) to peak?
    # For cpu-cpu: what's the time from first non-zero replica to peak?
    first_nonzero = None
    for i in range(len(ts_rep)):
        if reps[i] > 0.5:
            first_nonzero = ts_rep[i]
            break
    if first_nonzero and peak_ts:
        delta = (peak_ts - first_nonzero).total_seconds()
        print(f"    G) First non-zero replica → Peak: {delta:.0f}s")
    
    # Time from the moment test started loading to peak?
    # For cpu-cpu: the k6 ramp-up starts immediately. But when does the CPU first spike?
    # Let me check: the first CPU reading at 18:01:00 is 44m, then 111m, 123m, 269m...
    # The spike happens at 18:01:45. If test started ~17:59:00, that's 165s of ramp-up.
    # Peak replicas at 18:02:00. So from first spike to peak = 15s? No, that's too short.
    
    # ACTUALLY: the thesis says ∼150s for cpu-cpu. 
    # 150s from 17:59:00 = 18:01:30. At 18:01:30 we're at 21 replicas, not peak (29).
    # Peak is at 18:02:00, which is 180s from 17:59:00.
    
    # Let me check: maybe it's from the FIRST replica going above baseline to peak?
    # Baseline = 1 replica. First > 1 is at 18:00:30 (1→3). 18:00:30 to 18:02:00 = 90s.
    # That doesn't match 150s either.
    
    # What about: from k6 test start to when HPA stops scaling?
    # k6 test likely started around 17:58:30 (allowing time for setup).
    # If test started at 17:58:00, then peak at 18:02:00 = 240s. Still not 150.
    
    # Let me check a different hypothesis: what if "czas do skalowania" is:
    # from when HPA FIRST detects need to scale (DesiredReplicas > CurrentReplicas)
    # to when the LAST new pod becomes ready?
    # The HPA detection time is NOT in our CSV data.
    
    # THE MOST LIKELY: The ∼150s was originally computed as:
    # Time from when replicas first exceeded 1 (1→3 at 18:00:30) 
    # to when they reached peak (29 at 18:02:00) = 90s.
    # PLUS some adjustment or different run.
    
    # OR: it was simply estimated/approximated visually from the charts.
    
    # For io-cpu: first > 1 at 18:17:00 (1→2), peak at 18:19:00 (7) = 120s.
    # That doesn't match 135s either.
    
    # Let me check: time from first non-baseline replica (2+) to peak:
    # cpu-cpu: 18:00:30 to 18:02:00 = 90s (thesis says 150s)
    # io-cpu: 18:17:00 to 18:19:00 = 120s (thesis says 135s)

    print(f"\n  ── RECONCILIATION ATTEMPT ──")
    if first_nonzero and peak_ts:
        delta = (peak_ts - first_nonzero).total_seconds()
        # For cpu-cpu: 18:02:00 - 17:59:30 = 150s! BINGO!
        # For io-cpu: let me check...
        print(f"    First replica data → peak: {delta:.0f}s")

def main():
    print("DEEP-DIVE: Understanding current time-to-scale values")
    
    analyze_test('test3', 125, 'CPU')
    analyze_test('test4', 50, 'I_O')
    
    # Now let me look at it from the OTHER side: compute the time that
    # would give 150s for cpu-cpu and 135s for io-cpu
    print(f"\n\n{'='*60}")
    print("REVERSE-ENGINEER: What gives 150s for cpu-cpu?")
    print(f"{'='*60}")
    
    for test_name, label, threshold in [('test3', 'CPU', 125), ('test4', 'I_O', 50)]:
        test_dir = RESULTS_DIR / test_name
        replicas_csv = find_csv(test_dir, "Liczba aktywnych replik")
        ts_rep, reps = parse_replicas_csv(replicas_csv)
        
        peak_ts, peak_r = find_peak_ts(ts_rep, reps)
        print(f"\n  {test_name}: Peak at {peak_ts} ({peak_r:.0f} replicas)")
        
        # Find what timestamp is 150s before peak for cpu-cpu, 135s for io-cpu
        target_delta = 150 if test_name == 'test3' else 135
        from datetime import timedelta
        reference = peak_ts - timedelta(seconds=target_delta)
        print(f"    {target_delta}s before peak = {reference}")
        
        # Check replica value at that time
        closest = min(enumerate(ts_rep), key=lambda x: abs((x[1] - reference).total_seconds()))
        print(f"    Closest replica sample: [{ts_rep[closest[0]]}] = {reps[closest[0]]:.0f}")
        
        # Also check: what if "czas do skalowania" is measured from replicas crossing 2 (minReplicas)?
        for i in range(len(ts_rep)):
            if reps[i] >= 3:  # first scale above minReplicas
                scale_up_ts = ts_rep[i]
                if peak_ts:
                    delta = (peak_ts - scale_up_ts).total_seconds()
                    print(f"    First replica >=3 at {scale_up_ts} ({reps[i]:.0f}), delta to peak: {delta:.0f}s")
                break
    
    print(f"\n{'='*60}")
    print("SUMMARY OF FINDINGS")
    print(f"{'='*60}")
    print(f"""
    KEY FINDING: The current time-to-scale values (~150s for cpu-cpu, ~135s for io-cpu)
    appear to be measured from the FIRST replica data point in Grafana to the PEAK 
    replica count, NOT from the threshold-crossing moment.
    
    This is problematic because:
    1. The "first replica data point" is when Grafana started recording,
       not when the test/load actually started
    2. The HPA threshold was crossed BEFORE the CPU data in Grafana shows it
       (Grafana's 15s granularity masks the exact crossing moment)
    3. The correct methodology per the thesis equation would measure from
       CPU threshold crossing to peak, but the CSV data doesn't allow this
       because CPU monitoring starts after scaling has already begun
    
    RECOMMENDATION: The time-to-scale values need to be either:
    a) Recalculated from the replicas CSV: time from first scale-up event (replicas > minReplicas)
       to peak replicas. This gives ~90s for cpu-cpu and ~120s for io-cpu.
    b) Kept as-is but with a different definition (e.g., "całkowity czas od rozpoczęcia
       testu do osiągnięcia szczytowej liczby replik")
    c) The equation and narrative need to be updated to reflect the actual measurement.
    """)

if __name__ == '__main__':
    main()
