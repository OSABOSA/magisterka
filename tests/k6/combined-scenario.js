/**
 * combined-scenario.js — k6 combined load test for CPU and IO services
 *
 * Simulates realistic traffic hitting CPU and IO services simultaneously.
 *
 * Three scenarios running in parallel:
 *   cpu_fibonacci   – Fibonacci ramp-up (30 VUs, 300s total)
 *   cpu_processing  – Image processing constant (5 VUs, starts at 30s)
 *   io_queries      – IO queries ramp-up (80 VUs, 300s total)
 *
 * Configuration:
 *   CPU_URL    – base URL of the CPU-service    (default: http://cpu.magisterka.local)
 *   IO_URL     – base URL of the IO-service     (default: http://io.magisterka.local)
 *
 * Usage:
 *   k6 run combined-scenario.js
 *   k6 run -e CPU_URL=http://localhost:8080 -e IO_URL=http://localhost:8081 combined-scenario.js
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import encoding from 'k6/encoding';

// ---------------------------------------------------------------------------
// Configuration — separate base URLs per service
// ---------------------------------------------------------------------------

const CPU_URL = __ENV.CPU_URL || 'http://cpu.magisterka.local';
const IO_URL = __ENV.IO_URL || 'http://io.magisterka.local';

// ---------------------------------------------------------------------------
// Shared resources
// ---------------------------------------------------------------------------

// Minimal valid 1x1 white PNG (base64-encoded) for image processing
const MINIMAL_PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg==';

const PNG_BYTES = encoding.b64decode(MINIMAL_PNG_B64);

// ---------------------------------------------------------------------------
// k6 options — three concurrent scenarios
// ---------------------------------------------------------------------------

export const options = {
  scenarios: {
    cpu_fibonacci: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '60s', target: 30 },
        { duration: '180s', target: 30 },
        { duration: '60s', target: 0 },
      ],
      exec: 'fibonacci',
      gracefulRampDown: '30s',
    },
    cpu_processing: {
      executor: 'constant-vus',
      vus: 5,
      duration: '300s',
      exec: 'processImage',
      startTime: '30s',
      gracefulRampDown: '30s',
    },
    io_queries: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '60s', target: 80 },
        { duration: '180s', target: 80 },
        { duration: '60s', target: 0 },
      ],
      exec: 'ioQuery',
      gracefulRampDown: '30s',
    },
  },

  thresholds: {
    'http_req_duration{scenario:cpu_fibonacci}': ['p(95)<5000'],
    'http_req_duration{scenario:cpu_processing}': ['p(95)<10000'],
    'http_req_duration{scenario:io_queries}': ['p(95)<10000'],
    'http_req_failed': ['rate<0.05'],
  },
};

// ---------------------------------------------------------------------------
// cpu_fibonacci — Fibonacci computation (like cpu-load-test.js Scenario A)
// ---------------------------------------------------------------------------

export function fibonacci() {
  const url = `${CPU_URL}/fibonacci?n=25`;

  const res = http.get(url);

  const passed = check(res, {
    'fibonacci: status is 200': (r) => r.status === 200,
    'fibonacci: has computation_time_ms': (r) => {
      try {
        const body = JSON.parse(r.body);
        return typeof body.computation_time_ms === 'number';
      } catch (_) {
        return false;
      }
    },
  });

  if (!passed) {
    console.warn(`[combined] fibonacci FAIL — status=${res.status}, body=${res.body.substring(0, 200)}`);
  }

  sleep(0.2 + Math.random() * 0.3); // 0.2–0.5 s
}

// ---------------------------------------------------------------------------
// cpu_processing — Image processing (like cpu-load-test.js Scenario B)
// ---------------------------------------------------------------------------

export function processImage() {
  const width = 400;
  const height = 300;
  const filterOptions = ['blur', 'sharpen', 'edge_enhance'];
  const filter = filterOptions[Math.floor(Math.random() * filterOptions.length)];

  const url = `${CPU_URL}/process?width=${width}&height=${height}&filter=${filter}`;

  const formData = {
    file: http.file(PNG_BYTES, 'test_image.png', 'image/png'),
  };

  const res = http.post(url, formData);

  const passed = check(res, {
    'processImage: status is 200': (r) => r.status === 200,
    'processImage: has processing_time_ms': (r) => {
      try {
        const body = JSON.parse(r.body);
        return typeof body.processing_time_ms === 'number';
      } catch (_) {
        return false;
      }
    },
    'processImage: service is cpu-service': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.service === 'cpu-service';
      } catch (_) {
        return false;
      }
    },
  });

  if (!passed) {
    console.warn(`[combined] processImage FAIL — status=${res.status}, body=${res.body.substring(0, 200)}`);
  }

  sleep(0.5 + Math.random() * 0.5); // 0.5–1.0 s
}

// ---------------------------------------------------------------------------
// io_queries — IO query (like io-load-test.js Scenario A)
// ---------------------------------------------------------------------------

export function ioQuery() {
  const delay = 200;
  const steps = 3;
  const url = `${IO_URL}/query?delay=${delay}&steps=${steps}&external_call=false`;

  const res = http.get(url);

  const passed = check(res, {
    'ioQuery: status is 200': (r) => r.status === 200,
    'ioQuery: steps_completed == 3': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.steps_completed === steps;
      } catch (_) {
        return false;
      }
    },
  });

  if (!passed) {
    console.warn(`[combined] ioQuery FAIL — status=${res.status}, body=${res.body.substring(0, 200)}`);
  }

  sleep(0.1 + Math.random() * 0.2); // 0.1–0.3 s
}
