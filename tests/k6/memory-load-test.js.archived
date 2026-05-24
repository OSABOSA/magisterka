/**
 * memory-load-test.js — k6 load test for Memory-service
 *
 * Three scenarios:
 *   A) Cache Fill        — 5 VUs × 1 iteration each: fill 500 entries, then read stats
 *   B) Cache Read-heavy  — 50 VUs, 180s: read random cached keys
 *   C) Cache Mixed R/W   — 30 VUs, 120s: 70 % reads, 30 % writes
 *
 * Configuration:
 *   BASE_URL       – base URL of the Memory-service (default: http://memory.magisterka.local)
 *
 * Usage:
 *   k6 run memory-load-test.js
 *   k6 run -e BASE_URL=http://localhost:8000 memory-load-test.js
 */

import http from 'k6/http';
import { check, sleep } from 'k6';

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const BASE_URL = __ENV.BASE_URL || 'http://memory.magisterka.local';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Generate a random hex string of `len` characters.
 * Used to build cache keys matching the pattern created by /cache/fill
 * (i.e. "session_" + uuid hex prefix).
 */
function randomHex(len) {
  var chars = '0123456789abcdef';
  var result = '';
  for (var i = 0; i < len; i++) {
    result += chars[Math.floor(Math.random() * chars.length)];
  }
  return result;
}

// ---------------------------------------------------------------------------
// k6 options
// ---------------------------------------------------------------------------

export const options = {
  scenarios: {
    // Scenario A — Cache Fill (one-shot per VU, low VUs)
    cache_fill: {
      executor: 'per-vu-iterations',
      vus: 5,
      iterations: 1,
      exec: 'cacheFill',
      gracefulRampDown: '10s',
    },

    // Scenario B — Cache Read-heavy (sustained reads)
    cache_read: {
      executor: 'constant-vus',
      vus: 50,
      duration: '180s',
      exec: 'cacheRead',
      gracefulRampDown: '30s',
    },

    // Scenario C — Cache Mixed R/W
    cache_mixed: {
      executor: 'constant-vus',
      vus: 30,
      duration: '120s',
      exec: 'cacheMixed',
      gracefulRampDown: '30s',
    },
  },

  thresholds: {
    // 95th percentile of request duration < 3 s
    'http_req_duration': ['p(95)<3000'],
    // Less than 1 % failed requests
    'http_req_failed': ['rate<0.01'],
  },
};

// ---------------------------------------------------------------------------
// Scenario A — Cache Fill + Stats
// ---------------------------------------------------------------------------

export function cacheFill() {
  // Step 1: Fill cache with 500 entries of ~200 KB each (≈100 MB total)
  const fillUrl = `${BASE_URL}/cache/fill?sessions=500&size_kb=200`;

  const fillRes = http.post(fillUrl, null);

  const fillPassed = check(fillRes, {
    'cacheFill: POST /cache/fill status is 200': (r) => r.status === 200,
    'cacheFill: entries_added > 0': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.entries_added > 0;
      } catch (_) {
        return false;
      }
    },
  });

  if (!fillPassed) {
    console.warn(`cacheFill FAIL — status=${fillRes.status}, body=${fillRes.body.substring(0, 200)}`);
    return;
  }

  // Small pause to let the service settle
  sleep(0.5);

  // Step 2: Read statistics
  const statsUrl = `${BASE_URL}/stats`;
  const statsRes = http.get(statsUrl);

  const statsPassed = check(statsRes, {
    'cacheFill: GET /stats status is 200': (r) => r.status === 200,
    'cacheFill: stats has total_entries > 0': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.total_entries > 0;
      } catch (_) {
        return false;
      }
    },
    'cacheFill: stats has hit_ratio': (r) => {
      try {
        const body = JSON.parse(r.body);
        return typeof body.hit_ratio === 'number';
      } catch (_) {
        return false;
      }
    },
  });

  if (!statsPassed) {
    console.warn(`cacheFill stats FAIL — status=${statsRes.status}, body=${statsRes.body.substring(0, 200)}`);
  }

  sleep(0.3);
}

// ---------------------------------------------------------------------------
// Scenario B — Cache Read-heavy
//   Reads keys matching the pattern "session_XXXXXXXX" (8 random hex chars).
//   Some keys will hit (if cache was filled), others will be misses — both are
//   valid and help measure real-world hit/miss ratios.
// ---------------------------------------------------------------------------

export function cacheRead() {
  // Generate a key matching the /cache/fill pattern
  const key = 'session_' + randomHex(8);
  const url = `${BASE_URL}/cache/${key}`;

  const res = http.get(url);

  // A 404 is valid (cache miss) — we only flag unexpected errors
  const passed = check(res, {
    'cacheRead: status is 200 or 404': (r) => r.status === 200 || r.status === 404,
  });

  if (!passed) {
    console.warn(`cacheRead FAIL (key=${key}) — status=${res.status}, body=${res.body.substring(0, 200)}`);
  }

  sleep(0.1 + Math.random() * 0.2); // 0.1–0.3 s
}

// ---------------------------------------------------------------------------
// Scenario C — Cache Mixed R/W (70 % read, 30 % write)
// ---------------------------------------------------------------------------

export function cacheMixed() {
  // 70 % read, 30 % write
  const isRead = Math.random() < 0.7;

  // Key matching the same pattern as /cache/fill
  const key = 'session_' + randomHex(8);

  if (isRead) {
    // ---- READ ----
    const url = `${BASE_URL}/cache/${key}`;
    const res = http.get(url);

    const passed = check(res, {
      'cacheMixed read: status is 200 or 404': (r) => r.status === 200 || r.status === 404,
    });

    if (!passed) {
      console.warn(`cacheMixed read FAIL (key=${key}) — status=${res.status}`);
    }
  } else {
    // ---- WRITE ----
    const url = `${BASE_URL}/cache/${key}`;
    const payload = JSON.stringify({
      value: 'test_data_mixed_rw_scenario',
      size_kb: 10,
    });
    const params = { headers: { 'Content-Type': 'application/json' } };

    const res = http.post(url, payload, params);

    // 201 = created, 200 = updated existing, 507 = cache full (acceptable under load)
    const passed = check(res, {
      'cacheMixed write: status is 200, 201, or 507': (r) =>
        r.status === 200 || r.status === 201 || r.status === 507,
    });

    if (!passed) {
      console.warn(`cacheMixed write FAIL (key=${key}) — status=${res.status}, body=${res.body.substring(0, 200)}`);
    }
  }

  sleep(0.1 + Math.random() * 0.3); // 0.1–0.4 s
}
