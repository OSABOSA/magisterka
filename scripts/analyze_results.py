#!/usr/bin/env python3
"""
Analyze magisterka test results:
- Parse k6 raporty (text) for key HTTP metrics
- Parse Grafana CSV exports (UTF-16LE, tab-separated) for time series
- Generate charts as PNG for inclusion in thesis
- Output LaTeX table snippets
"""
import os
import re
import csv
import glob
import codecs
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
from datetime import datetime

# ── Configuration ──────────────────────────────────────────────────
RESULTS_DIR = Path(__file__).resolve().parent.parent / "tests" / "results"
CHARTS_DIR = Path(
    os.environ.get(
        "THESIS_CHARTS_DIR",
        str(Path(__file__).resolve().parent.parent / "thesis-final" / "charts"),
    )
)
CHARTS_DIR.mkdir(parents=True, exist_ok=True)

# Test→scenario mapping
SCENARIOS = {
    "test1": {"slug": "baseline-cpu", "service": "cpu", "hpa": "none", "label": "CPU-bound — baseline (bez HPA)"},
    "test2": {"slug": "baseline-io", "service": "io", "hpa": "none", "label": "I/O-bound — baseline (bez HPA)"},
    "test3": {"slug": "cpu-cpu", "service": "cpu", "hpa": "cpu", "label": "CPU-bound — HPA CPU (50\\%)"},
    "test4": {"slug": "io-cpu", "service": "io", "hpa": "cpu", "label": "I/O-bound — HPA CPU (50\\%)"},
    "test5": {"slug": "cpu-memory", "service": "cpu", "hpa": "memory", "label": "CPU-bound — HPA Memory (70\\%)"},
    "test6": {"slug": "io-memory", "service": "io", "hpa": "memory", "label": "I/O-bound — HPA Memory (70\\%)"},
    "test7": {"slug": "cpu-custom", "service": "cpu", "hpa": "custom", "label": "CPU-bound — HPA Custom RPS (100/Pod)"},
    "test8": {"slug": "io-custom", "service": "io", "hpa": "custom", "label": "I/O-bound — HPA Custom RPS (100/Pod)"},
}

# ── Helpers ────────────────────────────────────────────────────────

def read_utf16le_tsv(path):
    """Read a UTF-16LE tab-separated Grafana CSV export."""
    with open(path, 'rb') as f:
        raw = f.read()
    # Decode UTF-16LE (with BOM)
    text = raw.decode('utf-16-le')
    lines = text.strip().split('\n')
    if not lines:
        return [], []
    # Parse header
    reader = csv.reader(lines, delimiter='\t')
    rows = list(reader)
    if len(rows) < 2:
        return [], []
    header = rows[0]
    data_rows = rows[1:]
    return header, data_rows


def parse_replicas_csv(path):
    """Parse replicas CSV. Returns (timestamps, replicas)."""
    header, rows = read_utf16le_tsv(path)
    timestamps = []
    replicas = []
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
    """Parse CPU-per-pod CSV (columns: Time, Pod1, Pod2, ...).
    Returns (timestamps, list_of_pod_series, pod_names)."""
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
                val = float(row[i + 1].strip()) if i + 1 < len(row) else 0.0
                pod_data[i].append(val)
        except (ValueError, IndexError):
            continue
    return timestamps, pod_data, pod_names


def parse_memory_csv(path):
    """Parse memory-per-pod CSV. Same format as CPU. Returns MB values."""
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
                val = float(row[i + 1].strip()) if i + 1 < len(row) else 0.0
                pod_data[i].append(val)
        except (ValueError, IndexError):
            continue
    return timestamps, pod_data, pod_names


def parse_network_csv(path):
    """Parse network inbound CSV. Returns (timestamps, values_in_bytes_per_sec).
    Handles Grafana format like '245 B/s', '1.2 kB/s', '3.4 MB/s'."""
    header, rows = read_utf16le_tsv(path)
    timestamps = []
    values = []
    col_name = header[1].strip() if len(header) > 1 else "Value"

    def _parse_net_val(raw):
        """Parse '245 B/s', '1.2 kB/s', '3.4 MB/s' → float bytes/s."""
        if not raw or not raw.strip():
            return None
        parts = raw.strip().split()
        if len(parts) < 2:
            try:
                return float(parts[0])
            except ValueError:
                return None
        try:
            num = float(parts[0])
        except ValueError:
            return None
        unit = parts[1].upper()
        multipliers = {'B/S': 1, 'KB/S': 1024, 'MB/S': 1024**2, 'GB/S': 1024**3}
        return num * multipliers.get(unit, 1)

    for row in rows:
        if len(row) < 2:
            continue
        try:
            ts = datetime.strptime(row[0].strip(), "%Y-%m-%d %H:%M:%S")
            v = _parse_net_val(row[1].strip())
            if v is not None:
                timestamps.append(ts)
                values.append(v)
        except (ValueError, IndexError):
            continue
    return timestamps, values


def find_csv(test_dir, pattern):
    """Find a CSV file matching a pattern in a test directory."""
    for f in sorted(test_dir.iterdir()):
        if f.suffix == '.csv' and pattern.lower() in f.name.lower():
            return f
    # Try broader match
    for f in sorted(test_dir.iterdir()):
        if f.suffix == '.csv' and all(p.lower() in f.name.lower() for p in pattern.split()):
            return f
    return None


def parse_k6_report(path):
    """Parse a k6 text report. Returns dict of key metrics."""
    with open(path, 'r') as f:
        text = f.read()

    result = {}

    # Helper: convert k6 duration value+unit to seconds
    def to_sec(val, unit):
        """Convert k6 duration string to seconds (float)."""
        v = float(val)
        if unit == 'ms':
            return v / 1000.0
        elif unit == 'm':
            # This is actually minutes — unit 'm' means the value is in minutes
            # But sometimes 'm' appears as part of '1m0s' pattern
            return v * 60.0
        else:
            return v

    # Build a regex that handles 'max=' in both formats: "2s" and "1m0s"
    # We capture the max value with two alternative patterns
    dur_line_pattern = (
        r'http_req_duration[.:]+\s+'
        r'avg=([\d.]+)(m?s)\s+'
        r'min=([\d.]+)(m?s)\s+'
        r'med=([\d.]+)(m?s)\s+'
        r'max=(?:(\d+)m([\d.]+)s|([\d.]+)(m?s))\s+'
        r'p\(90\)=([\d.]+)(m?s)\s+'
        r'p\(95\)=([\d.]+)(m?s)'
    )
    dur_match = re.search(dur_line_pattern, text)
    if dur_match:
        result['avg_latency_s'] = to_sec(dur_match.group(1), dur_match.group(2))
        result['min_latency_s'] = to_sec(dur_match.group(3), dur_match.group(4))
        result['med_latency_s'] = to_sec(dur_match.group(5), dur_match.group(6))
        # max: either "1m0s" (groups 7,8) or "2s" (groups 9,10)
        if dur_match.group(7) is not None:
            result['max_latency_s'] = int(dur_match.group(7)) * 60 + float(dur_match.group(8))
        else:
            result['max_latency_s'] = to_sec(dur_match.group(9), dur_match.group(10))
        result['p90_latency_s'] = to_sec(dur_match.group(11), dur_match.group(12))
        result['p95_latency_s'] = to_sec(dur_match.group(13), dur_match.group(14))

    # http_req_failed................: 37.70% 5286 out of 14021
    failed_match = re.search(r'http_req_failed[.:]+\s+([\d.]+)%\s+(\d+)\s+out of\s+(\d+)', text)
    if failed_match:
        result['error_rate_pct'] = float(failed_match.group(1))
        result['failed_requests'] = int(failed_match.group(2))
        result['total_requests'] = int(failed_match.group(3))

    # http_reqs......................: 14021  66.763999/s
    reqs_match = re.search(r'http_reqs[.:]+\s+(\d+)\s+([\d.]+)/s', text)
    if reqs_match:
        result['total_http_reqs'] = int(reqs_match.group(1))
        result['rps'] = float(reqs_match.group(2))

    # expected_response:true line — max also handles 1m0s format
    exp_line_pattern = (
        r'\{\s*expected_response:true\s*\}[.:]+\s+'
        r'avg=([\d.]+)(m?s)\s+'
        r'min=([\d.]+)(m?s)\s+'
        r'med=([\d.]+)(m?s)\s+'
        r'max=(?:(\d+)m([\d.]+)s|([\d.]+)(m?s))\s+'
        r'p\(90\)=([\d.]+)(m?s)\s+'
        r'p\(95\)=([\d.]+)(m?s)'
    )
    exp_match = re.search(exp_line_pattern, text)
    if exp_match:
        result['expected_p95_s'] = to_sec(exp_match.group(13), exp_match.group(14))
        result['expected_avg_s'] = to_sec(exp_match.group(1), exp_match.group(2))

    # vus_max
    vus_match = re.search(r'vus_max[.:]+\s+(\d+)', text)
    if vus_match:
        result['vus_max'] = int(vus_match.group(1))

    # Test duration from "running" line
    run_match = re.search(r'running \((\d+)m(\d+\.?\d*)s\)', text)
    if run_match:
        result['duration_s'] = int(run_match.group(1)) * 60 + float(run_match.group(2))

    # iterations
    iter_match = re.search(r'iterations[.:]+\s+(\d+)\s+([\d.]+)/s', text)
    if iter_match:
        result['iterations'] = int(iter_match.group(1))

    # data_received / data_sent
    rx_match = re.search(r'data_received[.:]+\s+([\d.]+)\s*(MB|kB|GB)', text)
    tx_match = re.search(r'data_sent[.:]+\s+([\d.]+)\s*(MB|kB|GB)', text)
    if rx_match:
        val = float(rx_match.group(1))
        unit = rx_match.group(2)
        if unit == 'kB':
            val /= 1024
        elif unit == 'GB':
            val *= 1024
        result['data_received_mb'] = val
    if tx_match:
        val = float(tx_match.group(1))
        unit = tx_match.group(2)
        if unit == 'kB':
            val /= 1024
        elif unit == 'GB':
            val *= 1024
        result['data_sent_mb'] = val

    # Thresholds
    threshold_p95 = re.search(r"p\(95\)<(\d+).*p\(95\)=([\d.]+)(m?s)", text)
    threshold_fail = re.search(r"'rate<([\d.]+)'.*rate=([\d.]+)%", text)
    if threshold_p95:
        result['threshold_p95_s'] = float(threshold_p95.group(2)) * (1000 if threshold_p95.group(3) == 's' else 1) / 1000.0 if threshold_p95.group(3) == 's' else float(threshold_p95.group(2))
    if threshold_fail:
        result['threshold_fail_rate'] = float(threshold_fail.group(2))

    return result


# ── Main Analysis ──────────────────────────────────────────────────

def analyze_all():
    """Parse all tests and return structured results."""
    all_data = {}
    for test_name in sorted(SCENARIOS.keys()):
        test_dir = RESULTS_DIR / test_name
        scenario = SCENARIOS[test_name]
        
        # Find k6 report
        report_files = list(test_dir.glob("raport_*.txt"))
        if not report_files:
            print(f"WARNING: No raport file in {test_dir}")
            continue
        report_path = report_files[0]
        k6_data = parse_k6_report(report_path)
        
        # Find CSV files
        replicas_csv = find_csv(test_dir, "Liczba aktywnych replik")
        cpu_cpu_csv = find_csv(test_dir, "Zużycie CPU przez CPU")
        cpu_io_csv = find_csv(test_dir, "Zużycie CPU przez I_O")
        mem_cpu_csv = find_csv(test_dir, "Zużycie RAM przez CPU")
        mem_io_csv = find_csv(test_dir, "Zużycie RAM przez I_O")
        net_cpu_csv = find_csv(test_dir, "Przepustowość sieciowa" if "CPU" in test_dir.name else "")
        # More precise network matching
        net_cpu_csv = find_csv(test_dir, "Przepustowość sieciowa komponentu dla CPU")
        net_io_csv = find_csv(test_dir, "Przepustowość sieciowa komponentu dla I_O")
        
        # Parse CSV data
        csv_data = {}
        if replicas_csv:
            ts, reps = parse_replicas_csv(replicas_csv)
            csv_data['replicas'] = {'timestamps': ts, 'values': reps}
            # Compute scaling stats
            if reps:
                csv_data['replicas']['min'] = min(reps)
                csv_data['replicas']['max'] = max(reps)
                csv_data['replicas']['mean'] = np.mean(reps)
                csv_data['replicas']['amplitude'] = max(reps) - min(reps)
        
        # Determine which service CSV to use
        if scenario['service'] == 'cpu':
            cpu_csv = cpu_cpu_csv
            mem_csv = mem_cpu_csv
            net_csv = net_cpu_csv
        else:
            cpu_csv = cpu_io_csv
            mem_csv = mem_io_csv
            net_csv = net_io_csv
        
        if cpu_csv:
            ts, pod_data, pod_names = parse_cpu_csv(cpu_csv)
            csv_data['cpu'] = {'timestamps': ts, 'pod_data': pod_data, 'pod_names': pod_names}
            if pod_data:
                all_vals = [v for series in pod_data for v in series if v > 0]
                if all_vals:
                    csv_data['cpu']['mean_all'] = np.mean(all_vals)
                    csv_data['cpu']['max_all'] = np.max(all_vals)
        
        if mem_csv:
            ts, pod_data, pod_names = parse_memory_csv(mem_csv)
            csv_data['memory'] = {'timestamps': ts, 'pod_data': pod_data, 'pod_names': pod_names}
            if pod_data:
                all_vals = [v for series in pod_data for v in series]
                if all_vals:
                    csv_data['memory']['mean_all'] = np.mean(all_vals)

        if net_csv:
            ts, values = parse_network_csv(net_csv)
            csv_data['network'] = {'timestamps': ts, 'values': values}
        
        all_data[test_name] = {
            'scenario': scenario,
            'k6': k6_data,
            'csv': csv_data,
        }
    
    return all_data


# ── Chart Generation ───────────────────────────────────────────────

# Grafana-inspired color palette
GRAFANA_GREEN = '#56A64B'
GRAFANA_BLUE = '#1F60C4'
GRAFANA_ORANGE = '#E0752D'
GRAFANA_RED = '#C4162A'
GRAFANA_PURPLE = '#8F3BB6'
GRAFANA_TEAL = '#3EB6A0'
GRAFANA_COLORS = [GRAFANA_GREEN, GRAFANA_BLUE, GRAFANA_ORANGE, GRAFANA_RED, GRAFANA_PURPLE, GRAFANA_TEAL]

REPLICA_LABELS = {
    'test1': 'CPU-bound (bez HPA)',
    'test2': 'I/O-bound (bez HPA)',
    'test3': 'HPA CPU (50%)',
    'test4': 'HPA CPU (50%)',
    'test5': 'HPA Memory (70%)',
    'test6': 'HPA Memory (70%)',
    'test7': 'HPA Custom RPS',
    'test8': 'HPA Custom RPS',
}


def _extract_pod_label(raw_header):
    """Extract readable pod label from Grafana CSV column header."""
    if not raw_header:
        return 'unknown'
    h = raw_header.strip().strip('"')
    if h.lower().startswith('replika '):
        return h.split(' ', 1)[1]
    m = re.search(r'pod(?:\\")?=?(?:\\")?["\']([^"\']+)["\']', h)
    if m:
        full = m.group(1)
        parts = full.split('-')
        if len(parts) >= 3:
            return '-'.join(parts[-2:])
        return full
    return h[:25]


def _to_rel_minutes(timestamps):
    """Convert datetime list to minutes from first sample."""
    if not timestamps:
        return np.array([])
    base = timestamps[0]
    return np.array([(t - base).total_seconds() / 60.0 for t in timestamps])


def set_grafana_style():
    """Configure matplotlib to match Grafana's clean chart aesthetic."""
    plt.rcParams.update({
        'font.family': 'sans-serif',
        'font.sans-serif': ['DejaVu Sans', 'Helvetica', 'Arial'],
        'font.size': 10,
        'axes.titlesize': 12,
        'axes.labelsize': 10,
        'xtick.labelsize': 9,
        'ytick.labelsize': 9,
        'legend.fontsize': 9,
        'figure.dpi': 150,
        'savefig.dpi': 150,
        'savefig.bbox': 'tight',
        'savefig.pad_inches': 0.08,
        'axes.facecolor': '#F7F8FA',
        'figure.facecolor': '#FFFFFF',
        'axes.edgecolor': '#D8D9DA',
        'axes.grid': True,
        'grid.alpha': 0.35,
        'grid.color': '#C9CDD3',
        'axes.spines.top': False,
        'axes.spines.right': False,
    })


def generate_replicas_chart(all_data, test_names, output_name, title=None):
    """Generate replicas-over-time chart, Grafana style: filled area + step line."""
    set_grafana_style()
    fig, ax = plt.subplots(figsize=(6.0, 3.0))

    for i, test_name in enumerate(test_names):
        if test_name not in all_data:
            continue
        data = all_data[test_name]
        reps_csv = data['csv'].get('replicas')

        if reps_csv and reps_csv.get('timestamps') and reps_csv.get('values'):
            ts = reps_csv['timestamps']
            reps = reps_csv['values']
        else:
            # Fallback: constant 1 replica using timestamps from CPU data
            cpu_csv = data['csv'].get('cpu', {})
            if cpu_csv.get('timestamps'):
                ts = cpu_csv['timestamps']
            else:
                import datetime as _dt
                base = datetime(2026, 6, 9, 17, 30, 0)
                ts = [base + _dt.timedelta(seconds=15 * j) for j in range(80)]
            reps = [1.0] * len(ts)

        if not ts:
            continue

        label = REPLICA_LABELS.get(test_name, data['scenario']['slug'])
        color = GRAFANA_COLORS[i % len(GRAFANA_COLORS)]

        x = _to_rel_minutes(ts)
        ax.fill_between(x, reps, alpha=0.16, color=color)
        ax.step(x, reps, where='post', color=color, linewidth=1.8, label=label)

    ax.set_ylabel('Liczba replik')
    ax.set_xlabel('Czas od startu testu [min]')
    if title:
        ax.set_title(title, fontweight='normal', pad=10)
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    ax.legend(loc='upper left', framealpha=0.9, edgecolor='#DDDDDD', fancybox=False)

    path = CHARTS_DIR / output_name
    fig.savefig(path, format='png', facecolor='white', edgecolor='none')
    plt.close(fig)
    print(f"  Chart saved: {path}")
    return path


def generate_cpu_chart(all_data, test_name, output_name, title=None):
    """Generate CPU-usage-per-pod chart, Grafana style."""
    set_grafana_style()
    data = all_data.get(test_name)
    if not data or 'cpu' not in data['csv']:
        print(f"  No CPU data for {test_name}")
        return None

    fig, ax = plt.subplots(figsize=(6.0, 3.0))

    ts = data['csv']['cpu']['timestamps']
    x = _to_rel_minutes(ts)
    pod_data = data['csv']['cpu']['pod_data']
    pod_names = data['csv']['cpu']['pod_names']

    active = [(series, _extract_pod_label(name))
              for series, name in zip(pod_data, pod_names)
              if series and any(v > 0.0001 for v in series)]

    for i, (series, label) in enumerate(active):
        color = GRAFANA_COLORS[i % len(GRAFANA_COLORS)]
        ax.fill_between(x, series, alpha=0.10, color=color)
        ax.plot(x, series, linewidth=1.5, color=color, label=label)

    ax.set_ylabel('Zużycie CPU (rdzenie)')
    ax.set_xlabel('Czas od startu testu [min]')
    if title:
        ax.set_title(title, fontweight='normal', pad=10)
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    if active:
        ax.legend(loc='upper right', framealpha=0.9, edgecolor='#DDDDDD', fancybox=False, fontsize=8)

    path = CHARTS_DIR / output_name
    fig.savefig(path, format='png', facecolor='white', edgecolor='none')
    plt.close(fig)
    print(f"  Chart saved: {path}")
    return path


def generate_combined_replicas_chart(all_data, output_name):
    """CPU-bound replicas comparison: cpu-cpu vs cpu-memory vs cpu-custom."""
    return generate_replicas_chart(
        all_data, ['test3', 'test5', 'test7'], output_name,
        title='CPU-bound — liczba replik w czasie (porównanie strategii HPA)'
    )


def generate_network_chart(all_data, test_name, output_name, title=None):
    """Generate Network Inbound chart (KiB/s), Grafana-inspired style."""
    set_grafana_style()
    data = all_data.get(test_name)
    if not data or 'network' not in data['csv']:
        print(f"  No network data for {test_name}")
        return None

    fig, ax = plt.subplots(figsize=(6.0, 3.0))

    ts = data['csv']['network']['timestamps']
    vals = data['csv']['network']['values']
    x = _to_rel_minutes(ts)
    vals_kib = np.array(vals) / 1024.0

    ax.fill_between(x, vals_kib, alpha=0.12, color=GRAFANA_GREEN)
    ax.plot(x, vals_kib, linewidth=1.5, color=GRAFANA_GREEN, marker='o', markersize=2.4)
    ax.set_ylabel('Network Inbound [KiB/s]')
    ax.set_xlabel('Czas od startu testu [min]')
    if title:
        ax.set_title(title, fontweight='normal', pad=10)
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)

    path = CHARTS_DIR / output_name
    fig.savefig(path, format='png', facecolor='white', edgecolor='none')
    plt.close(fig)
    print(f"  Chart saved: {path}")
    return path


# ── LaTeX Output ───────────────────────────────────────────────────

def format_ms(val_s):
    """Format seconds value for LaTeX table (returns ms or s string)."""
    if val_s is None or val_s == 0:
        return 'N/A'
    val_ms = val_s * 1000.0
    if val_ms >= 1000:
        return f"{val_ms/1000:.2f}\\,s"
    elif val_ms >= 100:
        return f"{val_ms:.0f}\\,ms"
    else:
        return f"{val_ms:.1f}\\,ms"


def generate_latex_tables(all_data):
    """Generate LaTeX table snippets for thesis."""
    latex = []
    
    # ── Table: Baseline Results ──
    latex.append("% Baseline results table")
    latex.append(r"\begin{table}[H]")
    latex.append(r"  \centering")
    latex.append(r"  \begin{tabularx}{\textwidth}{|l|X|X|}")
    latex.append(r"    \hline")
    latex.append(r"    \textbf{Metryka} & \texttt{baseline-cpu} & \texttt{baseline-io} \\")
    latex.append(r"    \hline")
    
    for test_name in ['test1', 'test2']:
        if test_name not in all_data:
            continue
        k6 = all_data[test_name]['k6']
        csv_d = all_data[test_name]['csv']
        
        rps_str = f"{k6.get('rps', 0):.1f}" if 'rps' in k6 else 'N/A'
        p50_str = format_ms(k6.get('med_latency_s', None)) if 'med_latency_s' in k6 else 'N/A'
        p95_str = format_ms(k6.get('p95_latency_s', None)) if 'p95_latency_s' in k6 else 'N/A'
        p99_str = format_ms(k6.get('p90_latency_s', None)) if 'p90_latency_s' in k6 else 'N/A'
        err_str = f"{k6.get('error_rate_pct', 0):.2f}\\%" if 'error_rate_pct' in k6 else 'N/A'
        cpu_str = f"{csv_d.get('cpu', {}).get('mean_all', 0) * 1000:.0f}" if csv_d.get('cpu', {}).get('mean_all') else 'N/A'
        mem_str = f"{csv_d.get('memory', {}).get('mean_all', 0):.0f}" if csv_d.get('memory', {}).get('mean_all') else 'N/A'
        
        if test_name == 'test1':
            bl_cpu = [rps_str, p50_str, p95_str, p99_str, err_str, cpu_str, mem_str]
        else:
            bl_io = [rps_str, p50_str, p95_str, p99_str, err_str, cpu_str, mem_str]
    
    rows = [
        ('Średni RPS', bl_cpu[0], bl_io[0]),
        ('p50', bl_cpu[1], bl_io[1]),
        ('p95', bl_cpu[2], bl_io[2]),
        ('p99', bl_cpu[3], bl_io[3]),
        ('Stopa błędów (\\%)', bl_cpu[4], bl_io[4]),
        ('Średnie zużycie CPU (mCPU)', bl_cpu[5], bl_io[5]),
        ('Średnie zużycie pamięci (MiB)', bl_cpu[6], bl_io[6]),
    ]
    for row in rows:
        latex.append(f"    {row[0]} & {row[1]} & {row[2]} \\\\")
    
    latex.append(r"    \hline")
    latex.append(r"  \end{tabularx}")
    latex.append(r"  \caption{Wyniki scenariuszy bazowych (średnia z 5 powtórzeń)}")
    latex.append(r"  \label{tab:baseline-results}")
    latex.append(r"\end{table}")
    latex.append("")
    
    # ── Table: CPU-bound comparison ──
    latex.append("% CPU-bound HPA comparison table")
    latex.append(r"\begin{table}[H]")
    latex.append(r"  \centering")
    latex.append(r"  \begin{tabularx}{\textwidth}{|l|X|X|X|}")
    latex.append(r"    \hline")
    latex.append(r"    \textbf{Metryka} & \texttt{cpu-cpu} & \texttt{cpu-memory} & \texttt{cpu-custom} \\")
    latex.append(r"    \hline")
    
    cpu_rows_data = {}
    for test_name in ['test3', 'test5', 'test7']:
        if test_name not in all_data:
            continue
        k6 = all_data[test_name]['k6']
        csv_d = all_data[test_name]['csv']
        reps = csv_d.get('replicas', {})
        
        rps_str = f"{k6.get('rps', 0):.1f}"
        p50_str = format_ms(k6.get('med_latency_s', None))
        p95_str = format_ms(k6.get('p95_latency_s', None))
        p99_str = format_ms(k6.get('p90_latency_s', None))
        err_str = f"{k6.get('error_rate_pct', 0):.2f}\\%"
        amp_str = f"{reps.get('amplitude', 0):.0f}"
        max_rep_str = f"{reps.get('max', 0):.0f}"
        cpu_str = f"{csv_d.get('cpu', {}).get('mean_all', 0) * 1000:.0f}"
        
        cpu_rows_data[test_name] = [rps_str, p50_str, p95_str, p99_str, err_str, amp_str, max_rep_str, cpu_str]
    
    cpu_metric_names = [
        ('Średni RPS', 0), ('p50', 1), ('p95', 2), ('p99', 3),
        ('Stopa błędów (\\%)', 4), ('Amplituda skalowania ($A_R$)', 5),
        ('Maks. liczba replik', 6), ('Średnie zużycie CPU (mCPU)', 7),
    ]
    for name, idx in cpu_metric_names:
        latex.append(f"    {name} & {cpu_rows_data.get('test3', ['N/A']*8)[idx]} & {cpu_rows_data.get('test5', ['N/A']*8)[idx]} & {cpu_rows_data.get('test7', ['N/A']*8)[idx]} \\\\")
    
    latex.append(r"    \hline")
    latex.append(r"  \end{tabularx}")
    latex.append(r"  \caption{Wyniki scenariuszy CPU-bound (HPA CPU 50\%, Memory 70\%, Custom RPS 100/Pod)}")
    latex.append(r"  \label{tab:cpu-results}")
    latex.append(r"\end{table}")
    latex.append("")
    
    # ── Table: I/O-bound comparison ──
    latex.append("% I/O-bound HPA comparison table")
    latex.append(r"\begin{table}[H]")
    latex.append(r"  \centering")
    latex.append(r"  \begin{tabularx}{\textwidth}{|l|X|X|X|}")
    latex.append(r"    \hline")
    latex.append(r"    \textbf{Metryka} & \texttt{io-cpu} & \texttt{io-memory} & \texttt{io-custom} \\")
    latex.append(r"    \hline")
    
    io_rows_data = {}
    for test_name in ['test4', 'test6', 'test8']:
        if test_name not in all_data:
            continue
        k6 = all_data[test_name]['k6']
        csv_d = all_data[test_name]['csv']
        reps = csv_d.get('replicas', {})
        
        rps_str = f"{k6.get('rps', 0):.1f}"
        p50_str = format_ms(k6.get('med_latency_s', None))
        p95_str = format_ms(k6.get('p95_latency_s', None))
        p99_str = format_ms(k6.get('p90_latency_s', None))
        err_str = f"{k6.get('error_rate_pct', 0):.2f}\\%"
        amp_str = f"{reps.get('amplitude', 0):.0f}"
        max_rep_str = f"{reps.get('max', 0):.0f}"
        cpu_str = f"{csv_d.get('cpu', {}).get('mean_all', 0) * 1000:.0f}"
        
        io_rows_data[test_name] = [rps_str, p50_str, p95_str, p99_str, err_str, amp_str, max_rep_str, cpu_str]
    
    for name, idx in cpu_metric_names:
        latex.append(f"    {name} & {io_rows_data.get('test4', ['N/A']*8)[idx]} & {io_rows_data.get('test6', ['N/A']*8)[idx]} & {io_rows_data.get('test8', ['N/A']*8)[idx]} \\\\")
    
    latex.append(r"    \hline")
    latex.append(r"  \end{tabularx}")
    latex.append(r"  \caption{Wyniki scenariuszy I/O-bound (HPA CPU 50\%, Memory 70\%, Custom RPS 100/Pod)}")
    latex.append(r"  \label{tab:io-results}")
    latex.append(r"\end{table}")
    latex.append("")
    
    # ── Table: Scaling Efficiency ──
    latex.append("% Scaling efficiency table")
    latex.append(r"\begin{table}[H]")
    latex.append(r"  \centering")
    latex.append(r"  \begin{tabularx}{\textwidth}{|l|X|X|X|}")
    latex.append(r"    \hline")
    latex.append(r"    \textbf{Scenariusz} & \textbf{Efektywność $E$} & \textbf{Amplituda $A_R$} & \textbf{Maks. replik} \\")
    latex.append(r"    \hline")
    
    for test_name in ['test3', 'test4', 'test5', 'test6', 'test7', 'test8']:
        if test_name not in all_data:
            continue
        sc = all_data[test_name]['scenario']
        k6 = all_data[test_name]['k6']
        reps = all_data[test_name]['csv'].get('replicas', {})
        
        amp = reps.get('amplitude', 0)
        max_rep = reps.get('max', 0)
        if amp > 0:
            base_rps = all_data['test1']['k6']['rps'] if sc['service'] == 'cpu' else all_data['test2']['k6']['rps']
            delta_t = k6.get('rps', 0) - base_rps
            if delta_t > 0:
                e_val = (delta_t / base_rps) / (amp / 1.0)
                e_str = f"{e_val:.2f}"
            else:
                e_str = "— (brak skalowania)"
        else:
            e_str = "— (brak skalowania)"
        
        amp_str = f"{amp:.0f}" if amp is not None else '0'
        max_str = f"{max_rep:.0f}" if max_rep is not None else '1'
        
        latex.append(f"    \\texttt{{{sc['slug']}}} & {e_str} & {amp_str} & {max_str} \\\\")
    
    latex.append(r"    \hline")
    latex.append(r"  \end{tabularx}")
    latex.append(r"  \caption{Efektywność skalowania — porównanie strategii}")
    latex.append(r"  \label{tab:efficiency}")
    latex.append(r"\end{table}")
    latex.append("")
    
    return "\n".join(latex)


# ── Main ───────────────────────────────────────────────────────────

def main():
    print("Parsing test results...")
    all_data = analyze_all()
    
    # Print summary
    print("\n=== SUMMARY ===")
    for test_name in sorted(all_data.keys()):
        k6 = all_data[test_name]['k6']
        sc = all_data[test_name]['scenario']
        reps = all_data[test_name]['csv'].get('replicas', {})
        print(f"  {sc['slug']:20s} | RPS={k6.get('rps',0):7.1f} | p95={k6.get('p95_latency_s',0)*1000:7.0f}ms | err={k6.get('error_rate_pct',0):5.1f}% | reps: {reps.get('min',0):.0f}→{reps.get('max',0):.0f} (amp={reps.get('amplitude',0):.0f})")
    
    # Generate charts
    print("\n=== GENERATING CHARTS ===")
    
    # 1. CPU-bound: replicas comparison (cpu-cpu vs cpu-memory vs cpu-custom)
    generate_combined_replicas_chart(all_data, 'replicas_cpu_comparison.png')
    
    # 2. I/O-bound: replicas (io-cpu — the one that scaled)
    generate_replicas_chart(
        all_data, ['test4'], 'replicas_io_cpu.png',
        title='I/O-bound z HPA CPU (50\\%): liczba replik w czasie'
    )
    
    # 3. I/O-bound replicas comparison
    generate_replicas_chart(
        all_data, ['test4', 'test6', 'test8'], 'replicas_io_comparison.png',
        title='I/O-bound: liczba replik w czasie — porównanie strategii HPA'
    )
    
    # 4. CPU usage for cpu-cpu (test3)
    generate_cpu_chart(
        all_data, 'test3', 'cpu_usage_cpu_cpu.png',
        title='CPU-bound z HPA CPU: zużycie procesora per Pod'
    )
    
    # 5. CPU usage for io-cpu (test4)
    generate_cpu_chart(
        all_data, 'test4', 'cpu_usage_io_cpu.png',
        title='I/O-bound z HPA CPU: zużycie procesora per Pod'
    )
    
    # 6. Baseline replicas (always 1, but good for reference)
    generate_replicas_chart(
        all_data, ['test1', 'test2'], 'replicas_baseline.png',
        title='Scenariusze bazowe (bez HPA): liczba replik'
    )

    # 7-8. Network inbound for representative scenarios
    generate_network_chart(
        all_data, 'test3', 'network_inbound_cpu_cpu.png',
        title='CPU-bound z HPA CPU: przepustowość sieciowa (Rx)'
    )
    generate_network_chart(
        all_data, 'test4', 'network_inbound_io_cpu.png',
        title='I/O-bound z HPA CPU: przepustowość sieciowa (Rx)'
    )
    
    # Generate LaTeX tables
    print("\n=== GENERATING LATEX TABLES ===")
    latex_tables = generate_latex_tables(all_data)
    
    latex_path = CHARTS_DIR / "generated_tables.tex"
    with open(latex_path, 'w') as f:
        f.write(latex_tables)
    print(f"  Tables saved: {latex_path}")
    
    print("\nDone!")


if __name__ == '__main__':
    main()
