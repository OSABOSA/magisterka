#!/usr/bin/env python3
"""
Verify time-to-scale calculations for thesis rozdzial5.tex.
Correct methodology: measure from moment metric crosses HPA threshold to pod creation.
"""
import os, sys, csv
from datetime import datetime

# ── CSV parsing (standalone, no imports from analyze_results) ──

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

def parse_cpu_csv(path):
    header, rows = read_utf16le_tsv(path)
    timestamps = []
    pod_names = [h.strip() for h in header[1:]]
    pod_data = [[] for _ in pod_names]
    for row in rows:
        if len(row) < 2:
            continue
        try:
            ts = datetime.strptime(row[0].strip(), "%Y-%m-%d %H:%M:%S")
            timestamps.append(ts)
            for i in range(len(pod_names)):
                val = float(row[i+1].strip()) if i+1 < len(row) else 0.0
                pod_data[i].append(val)
        except (ValueError, IndexError):
            continue
    return timestamps, pod_data, pod_names

def find_csv(test_dir, pattern):
    for f in sorted(test_dir.iterdir()):
        if f.suffix == '.csv' and pattern.lower() in f.name.lower():
            return f
    return None

# ── Main analysis ──

from pathlib import Path
RESULTS_DIR = Path(__file__).resolve().parent.parent / "tests" / "results"

TESTS = {
    'test3': {'slug': 'cpu-cpu', 'service': 'cpu', 'threshold_mcpu': 125, 'cpu_request': 250},
    'test4': {'slug': 'io-cpu', 'service': 'io', 'threshold_mcpu': 50, 'cpu_request': 100},
}

def analyze_test(test_name, cfg):
    test_dir = RESULTS_DIR / test_name
    service_label = 'CPU' if cfg['service'] == 'cpu' else 'I_O'
    
    cpu_csv = find_csv(test_dir, f"Zużycie CPU przez {service_label}")
    replicas_csv = find_csv(test_dir, "Liczba aktywnych replik")
    
    if not cpu_csv or not replicas_csv:
        print(f"  ERROR: Missing CSV files")
        return
    
    ts_cpu, pod_data, pod_names = parse_cpu_csv(cpu_csv)
    ts_rep, replicas = parse_replicas_csv(replicas_csv)
    
    print(f"\n  CPU CSV: {cpu_csv.name}")
    print(f"  Replicas CSV: {replicas_csv.name}")
    print(f"  CPU data points: {len(ts_cpu)}, Replica data points: {len(ts_rep)}")
    print(f"  Time range CPU: {ts_cpu[0] if ts_cpu else 'N/A'} → {ts_cpu[-1] if ts_cpu else 'N/A'}")
    print(f"  Time range Rep: {ts_rep[0] if ts_rep else 'N/A'} → {ts_rep[-1] if ts_rep else 'N/A'}")
    print(f"  Threshold: avg CPU > {cfg['threshold_mcpu']}m (50% of {cfg['cpu_request']}m request)")
    
    # ── Find threshold crossing ──
    print(f"\n  ── CPU samples (first 25) ──")
    threshold_crossed_ts = None
    threshold_crossed_avg = None
    threshold_crossed_n = None
    
    for i in range(min(60, len(ts_cpu))):
        pod_vals = []
        for series in pod_data:
            if i < len(series) and series[i] > 0.0001:
                pod_vals.append(series[i])
        if not pod_vals:
            continue
        avg_cpu = sum(pod_vals) / len(pod_vals)
        avg_mcpu = avg_cpu * 1000
        n_active = len(pod_vals)
        
        marker = " *** THRESHOLD CROSSED ***" if avg_mcpu > cfg['threshold_mcpu'] else ""
        if i < 25 or marker:
            print(f"    [{ts_cpu[i].strftime('%H:%M:%S')}] avg={avg_mcpu:6.1f}m n={n_active} | pods: {[f'{pod_data[j][i]*1000:.0f}' for j in range(min(len(pod_data), n_active+2)) if i < len(pod_data[j])]}{marker}")
        
        if threshold_crossed_ts is None and avg_mcpu > cfg['threshold_mcpu']:
            threshold_crossed_ts = ts_cpu[i]
            threshold_crossed_avg = avg_mcpu
            threshold_crossed_n = n_active
    
    if threshold_crossed_ts:
        print(f"\n  ✓ Threshold crossed at: {threshold_crossed_ts.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"    Avg CPU: {threshold_crossed_avg:.1f} mCPU, Active pods: {threshold_crossed_n}")
    else:
        print(f"\n  ✗ Threshold NEVER crossed!")
    
    # ── Find first pod increase ──
    print(f"\n  ── Replica changes ──")
    prev_rep = replicas[0] if replicas else None
    first_increase_ts = None
    for i in range(len(ts_rep)):
        if replicas[i] != prev_rep:
            print(f"    [{ts_rep[i].strftime('%H:%M:%S')}] {prev_rep:.0f} → {replicas[i]:.0f}")
            if first_increase_ts is None and replicas[i] > prev_rep:
                first_increase_ts = ts_rep[i]
            prev_rep = replicas[i]
    print(f"    Replica range: {min(replicas):.0f} → {max(replicas):.0f}")
    
    # ── Compute timing ──
    print(f"\n  ── TIMING RESULTS ──")
    if threshold_crossed_ts and first_increase_ts:
        delta = (first_increase_ts - threshold_crossed_ts).total_seconds()
        print(f"    Time from threshold crossing to first pod increase: {delta:.0f}s ({delta/60:.1f} min)")
        
        # Also find when replicas reach max
        max_reps = max(replicas)
        max_ts = None
        for i in range(len(ts_rep)):
            if replicas[i] == max_reps:
                max_ts = ts_rep[i]
                break
        if max_ts:
            delta2 = (max_ts - threshold_crossed_ts).total_seconds()
            print(f"    Time from threshold crossing to peak replicas ({max_reps:.0f}): {delta2:.0f}s ({delta2/60:.1f} min)")
    elif threshold_crossed_ts and not first_increase_ts:
        print(f"    Threshold crossed but no pod increase detected (check replica CSV granularity)")
    elif not threshold_crossed_ts:
        print(f"    Cannot compute — threshold never crossed")

def main():
    print("=" * 70)
    print("TIME-TO-SCALE VERIFICATION")
    print("=" * 70)
    
    for test_name, cfg in TESTS.items():
        print(f"\n{'─'*70}")
        print(f"TEST: {test_name} ({cfg['slug']})")
        print(f"{'─'*70}")
        analyze_test(test_name, cfg)
    
    print(f"\n{'='*70}")
    print("DONE")
    print("=" * 70)

if __name__ == '__main__':
    main()
