/**
 * io-load-test.js — k6 load test for IO-service
 *
 * Three scenarios:
 *   A) Constant delay, ramp-up VUs    — 1→100 VUs over 120s, hold 100 VUs for 180s
 *   B) Increasing delay (stress test) — 30 VUs, delay ramps from 50→2000 ms
 *   C) External calls                 — 20 VUs, calls /query with external_call=true
 *
 * Configuration:
 *   BASE_URL       – base URL of the IO-service (default: http://io.magisterka.local)
 *
 * Usage:
 *   k6 run io-load-test.js
 *   k6 run -e BASE_URL=http://localhost:8000 io-load-test.js
 */

import http from 'k6/http';
import { check, sleep } from 'k6';

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const BASE_URL = __ENV.BASE_URL || 'http://io.magisterka.local';

// ---------------------------------------------------------------------------
// Per-VU state for Scenario B (increasing delay stress test)
// ---------------------------------------------------------------------------

const vuState = {};

// ---------------------------------------------------------------------------
// k6 options
// ---------------------------------------------------------------------------

export const options = {
  scenarios: {
    // Scenario A — Constant delay, ramp-up VUs
    constant_query: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: [
        { duration: '120s', target: 100 },  // ramp-up
        { duration: '180s', target: 100 },   // steady
        { duration: '60s', target: 0 },      // ramp-down
      ],
      exec: 'constantQuery',
      gracefulRampDown: '30s',
    },

    // Scenario B — Increasing delay (stress test)
    stress_test: {
      executor: 'constant-vus',
      vus: 30,
      duration: '120s',
      exec: 'stressTest',
      gracefulRampDown: '30s',
    },

    // Scenario C — External calls
    external_calls: {
      executor: 'constant-vus',
      vus: 20,
      duration: '120s',
      exec: 'externalCalls',
      gracefulRampDown: '30s',
    },
  },

  thresholds: {
    // 95th percentile of request duration < 10 s
    'http_req_duration': ['p(95)<10000'],
    // Less than 5 % failed requests
    'http_req_failed': ['rate<0.05'],
  },
};

// ---------------------------------------------------------------------------
// Scenario A — Constant delay, ramp-up VUs
// ---------------------------------------------------------------------------

export function constantQuery() {
  const delay = 200;
  const steps = 3;
  const url = `${BASE_URL}/query?delay=${delay}&steps=${steps}&external_call=false`;

  const res = http.get(url);

  const passed = check(res, {
    'constantQuery: status is 200': (r) => r.status === 200,
    'constantQuery: steps_completed == 3': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.steps_completed === steps;
      } catch (_) {
        return false;
      }
    },
  });

  if (!passed) {
    console.warn(`constantQuery FAIL — status=${res.status}, body=${res.body.substring(0, 200)}`);
  }

  sleep(0.1 + Math.random() * 0.2); // 0.1–0.3 s
}

// ---------------------------------------------------------------------------
// Scenario B — Increasing delay (stress test)
//   Each VU linearly ramps delay from 50 ms to 2000 ms over the scenario duration.
// ---------------------------------------------------------------------------

export function stressTest() {
  // Initialise per-VU start time on first invocation
  if (!vuState[__VU]) {
    vuState[__VU] = { startTime: Date.now() };
  }

  const elapsedSec = (Date.now() - vuState[__VU].startTime) / 1000;
  const scenarioDuration = 120; // seconds — must match the scenario duration above
  const minDelay = 50;
  const maxDelay = 2000;

  // Linear interpolation: delay grows from 50→2000 over the full duration
  const delay = Math.floor(
    minDelay + (maxDelay - minDelay) * Math.min(elapsedSec / scenarioDuration, 1.0)
  );

  const steps = 2;
  const url = `${BASE_URL}/query?delay=${delay}&steps=${steps}&external_call=false`;

  const res = http.get(url);

  const passed = check(res, {
    'stressTest: status is 200': (r) => r.status === 200,
    'stressTest: steps_completed == 2': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.steps_completed === steps;
      } catch (_) {
        return false;
      }
    },
  });

  if (!passed) {
    console.warn(`stressTest FAIL (delay=${delay}ms) — status=${res.status}, body=${res.body.substring(0, 200)}`);
  }

  sleep(0.1 + Math.random() * 0.3); // 0.1–0.4 s
}

// ---------------------------------------------------------------------------
// Scenario C — External calls
// ---------------------------------------------------------------------------

export function externalCalls() {
  const delay = 50;
  const steps = 1;
  const url = `${BASE_URL}/query?delay=${delay}&steps=${steps}&external_call=true`;

  const res = http.get(url);

  const passed = check(res, {
    'externalCalls: status is 200': (r) => r.status === 200,
    'externalCalls: external_calls is true': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.external_calls === true;
      } catch (_) {
        return false;
      }
    },
    'externalCalls: steps_completed == 1': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.steps_completed === steps;
      } catch (_) {
        return false;
      }
    },
  });

  if (!passed) {
    console.warn(`externalCalls FAIL — status=${res.status}, body=${res.body.substring(0, 200)}`);
  }

  sleep(0.1 + Math.random() * 0.2); // 0.1–0.3 s
}
