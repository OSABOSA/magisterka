import http from 'k6/http';
import { check, sleep } from 'k6';
import encoding from 'k6/encoding';

// ---------------------------------------------------------------------------
// Configuration — separate base URLs per service
// ---------------------------------------------------------------------------

const BASE_URL = __ENV.BASE_URL || __ENV.CPU_URL || 'http://cpu.magisterka.local';

// ---------------------------------------------------------------------------
// Shared resources
// ---------------------------------------------------------------------------

// Minimal valid 1x1 white PNG (base64-encoded) for image processing
const MINIMAL_PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg==';

const PNG_BYTES = encoding.b64decode(MINIMAL_PNG_B64);

// ---------------------------------------------------------------------------
// k6 options — CPU scenarios only
// ---------------------------------------------------------------------------

export const options = {
  hosts: {
    'cpu.magisterka.local': '8.233.83.130',
    'io.magisterka.local': '8.233.83.130',
  },
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
  },

  thresholds: {
    'http_req_duration{scenario:cpu_fibonacci}': ['p(95)<5000'],
    'http_req_duration{scenario:cpu_processing}': ['p(95)<10000'],
    'http_req_failed': ['rate<0.05'],
  },
};

// ---------------------------------------------------------------------------
// cpu_fibonacci — Fibonacci computation
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
    console.warn(`[cpu-test] fibonacci FAIL — status=${res.status}, body=${res.body ? res.body.substring(0, 200) : 'empty'}`);
  }

  sleep(0.2 + Math.random() * 0.3); // 0.2–0.5 s
}

// ---------------------------------------------------------------------------
// cpu_processing — Image processing
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
    console.warn(`[cpu-test] processImage FAIL — status=${res.status}, body=${res.body ? res.body.substring(0, 200) : 'empty'}`);
  }

  sleep(0.5 + Math.random() * 0.5); // 0.5–1.0 s
}