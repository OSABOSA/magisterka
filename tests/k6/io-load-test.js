import http from 'k6/http';
import { check, sleep } from 'k6';

// ---------------------------------------------------------------------------
// Configuration — separate base URLs per service
// ---------------------------------------------------------------------------

const BASE_URL = __ENV.BASE_URL || __ENV.IO_URL || 'http://io.magisterka.local';

// ---------------------------------------------------------------------------
// k6 options — IO scenario only
// ---------------------------------------------------------------------------

export const options = {
  hosts: {
    'cpu.magisterka.local': '8.233.83.130',
    'io.magisterka.local': '8.233.83.130',
  },
  scenarios: {
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
    'http_req_duration{scenario:io_queries}': ['p(95)<10000'],
    'http_req_failed': ['rate<0.05'],
  },
};

// ---------------------------------------------------------------------------
// io_queries — IO query
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
    console.warn(`[io-test] ioQuery FAIL — status=${res.status}, body=${res.body ? res.body.substring(0, 200) : 'empty'}`);
  }

  sleep(0.1 + Math.random() * 0.2); // 0.1–0.3 s
}