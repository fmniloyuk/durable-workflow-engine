import http from 'k6/http';
import { check } from 'k6';

const API = __ENV.API_URL || 'http://localhost:8000';
const RATE = Number(__ENV.RATE || 20);
const DURATION = __ENV.DURATION || '30s';

export const options = {
  scenarios: {
    submit_workflows: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: 20,
      maxVUs: 200,
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<1500'],
  },
};

export default function () {
  const key = `k6-${__VU}-${__ITER}-${Date.now()}`;
  const body = JSON.stringify({
    name: 'k6-invoice',
    tasks: [
      { key: 'generate_invoice', kind: 'noop', queue: 'billing' },
      { key: 'charge_customer', kind: 'noop', queue: 'billing', depends_on: ['generate_invoice'] },
      { key: 'send_receipt', kind: 'noop', queue: 'email', depends_on: ['charge_customer'] },
      { key: 'update_analytics', kind: 'noop', queue: 'analytics', depends_on: ['send_receipt'] },
    ],
  });
  const response = http.post(`${API}/v1/workflows`, body, {
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
  });
  check(response, {
    'workflow accepted': (r) => r.status === 202,
    'workflow id returned': (r) => Boolean(r.json('id')),
  });
}

export function handleSummary(data) {
  const path = __ENV.SUMMARY_PATH || 'load/results/k6-summary.json';
  return {
    stdout: JSON.stringify(data.metrics, null, 2) + '\n',
    [path]: JSON.stringify(data, null, 2),
  };
}
