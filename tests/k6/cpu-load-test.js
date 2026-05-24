/**
 * cpu-load-test.js — k6 load test for CPU-service
 *
 * Two scenarios:
 *   A) Fibonacci — ramp-up 1→50 VUs over 60s, hold 50 VUs for 120s
 *   B) Image Processing — constant 10 VUs for 180s
 *
 * Configuration:
 *   BASE_URL       – base URL of the CPU-service (default: http://cpu.magisterka.local)
 *
 * Usage:
 *   k6 run cpu-load-test.js
 *   k6 run -e BASE_URL=http://localhost:8000 cpu-load-test.js
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import encoding from 'k6/encoding';

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const BASE_URL = __ENV.BASE_URL || 'http://cpu.magisterka.local';

// ---------------------------------------------------------------------------
// Minimal valid 1x1 white PNG (base64-encoded) for image upload tests.
// Pillow will resize it to requested dimensions anyway.
// ---------------------------------------------------------------------------

const MINIMAL_PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg==';

// Decoded PNG bytes (shared across VUs for efficiency)
const PNG_BYTES = encoding.b64decode(MINIMAL_PNG_B64);

// ---------------------------------------------------------------------------
// k6 options
// ---------------------------------------------------------------------------

export const options = {
  scenarios: {
    // Scenario A — Fibonacci ramp-up
    fibonacci: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: [
        { duration: '60s', target: 50 },   // ramp-up
        { duration: '120s', target: 50 },  // steady
        { duration: '30s', target: 0 },    // ramp-down
      ],
      exec: 'fibonacci',
      gracefulRampDown: '30s',
    },

    // Scenario B — Image processing at constant load
    process_image: {
      executor: 'constant-vus',
      vus: 10,
      duration: '180s',
      exec: 'processImage',
      gracefulRampDown: '30s',
    },
  },

  thresholds: {
    // 95th percentile of request duration < 5 s
    'http_req_duration': ['p(95)<5000'],
    // Less than 1 % failed requests
    'http_req_failed': ['rate<0.01'],
  },
};

// ---------------------------------------------------------------------------
// Scenario A — Fibonacci computation
// ---------------------------------------------------------------------------

export function fibonacci() {
  // Use n=25 as specified — moderate CPU load, well within the 0-40 range
  const url = `${BASE_URL}/fibonacci?n=25`;

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
    console.warn(`fibonacci FAIL — status=${res.status}, body=${res.body.substring(0, 200)}`);
  }

  // Realistic inter-request delay
  sleep(0.2 + Math.random() * 0.3); // 0.2–0.5 s
}

// ---------------------------------------------------------------------------
// Scenario B — Image processing
// ---------------------------------------------------------------------------

export function processImage() {
  const width = 400;
  const height = 300;
  const filterOptions = ['blur', 'sharpen', 'edge_enhance'];
  const filter = filterOptions[Math.floor(Math.random() * filterOptions.length)];

  const url = `${BASE_URL}/process?width=${width}&height=${height}&filter=${filter}`;

  // Build multipart form with the minimal PNG
  const formData = {
    file: http.file(PNG_BYTES, 'test_image.png', 'image/png'),
  };

  const res = http.post(url, formData);

  const passed = check(res, {
    'process: status is 200': (r) => r.status === 200,
    'process: has processing_time_ms': (r) => {
      try {
        const body = JSON.parse(r.body);
        return typeof body.processing_time_ms === 'number';
      } catch (_) {
        return false;
      }
    },
    'process: has valid stats': (r) => {
      try {
        const body = JSON.parse(r.body);
        return (
          body.service === 'cpu-service' &&
          Array.isArray(body.stats.mean_rgb) &&
          body.stats.mean_rgb.length === 3
        );
      } catch (_) {
        return false;
      }
    },
  });

  if (!passed) {
    console.warn(`process FAIL — status=${res.status}, body=${res.body.substring(0, 200)}`);
  }

  // Image processing is heavier — slightly longer sleep
  sleep(0.5 + Math.random() * 0.5); // 0.5–1.0 s
}
