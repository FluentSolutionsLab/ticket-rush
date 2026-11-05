// S1 Flash sale (Block 1, weeks 1, 2, 5, 6). See docs/specs/block-1-data-and-performance.md.
// Run: k6 run -e BASE_URL=http://localhost:8080 -e VUS=1000 --summary-export load/results/w1-naive.json load/s1-flash-sale.js
import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

const holds201 = new Counter('holds_201');
const holds409 = new Counter('holds_409');
const BASE = __ENV.BASE_URL || 'http://localhost:8080';
const VUS = Number(__ENV.VUS || 1000); // start at 1,000; go to 10,000 only once the machine is stable

export const options = {
  scenarios: {
    flash_sale: {
      executor: 'ramping-vus',
      stages: [
        { duration: '10s', target: VUS },
        { duration: '50s', target: VUS },
      ],
    },
  },
  thresholds: {
    'http_req_duration{name:hold}': ['p(99)<1000'],
    'http_req_failed{name:hold}': ['rate<0.01'], // 201 and 409 count as expected responses
  },
};

export default function () {
  const userId = __VU * 100000 + __ITER;
  const headers = { 'X-User-Id': `${userId}`, 'Content-Type': 'application/json' };
  http.get(`${BASE}/events/1/availability`, { headers, tags: { name: 'availability' } });

  const seat = 1 + Math.floor(Math.random() * 500);
  const res = http.post(`${BASE}/events/1/holds`, JSON.stringify({ seatIds: [seat] }), {
    headers,
    tags: { name: 'hold' },
    responseCallback: http.expectedStatuses(201, 409),
  });
  if (res.status === 201) holds201.add(1);
  if (res.status === 409) holds409.add(1);
  check(res, { 'no 5xx': (r) => r.status < 500 });
}
