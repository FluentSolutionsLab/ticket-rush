# Ticket Rush — Block 1 Spec

_Spec as of 2026-11-05. Source: https://claude.ai/artifact/SAZB4xvYbtcP7xi9y8ZDYM_

## Overview

Ticket Rush is a concert-ticket booking API where 10,000 users fight for 500 seats in the first 60 seconds of a sale. Over 6 weeks you build it naively, break it under load, and fix it one bottleneck at a time, measuring every change.

**By the end of Block 1 you can:**

- Explain and benchmark 3 concurrency-control strategies (pessimistic, optimistic, Redis reservation) and pick one with numbers.
- Use caching safely: cache-aside, invalidation, TTLs, and stampede protection.
- Reason about replication lag and route reads so users always see their own writes.
- Read query plans, design indexes, and size connection pools.
- Make write endpoints idempotent and protect the system with distributed rate limiting.

**In scope:** one ASP.NET Core service, Postgres (primary + 1 replica), Redis, k6, Prometheus and Grafana, all in Docker Compose.

**Out of scope (later blocks):** payments, message queues, microservices, auth beyond a fake `X-User-Id` header, and cloud deployment.

## Tech stack and local environment

Everything runs locally with one `docker compose up`, so experiments are cheap to reset and repeat.

| Component | Choice | Notes |
| --- | --- | --- |
| API | ASP.NET Core (.NET 8 or later), minimal APIs or controllers | Run 2 instances behind Nginx from week 3 on |
| Data access | EF Core + Npgsql; Dapper for hot paths | Compare both in week 5 |
| Database | Postgres 16+, primary + 1 streaming replica | Replica added in week 4 |
| Cache / locks | Redis 7 (StackExchange.Redis) | Used for caching, reservations, and rate limiting |
| Load testing | k6 | Scripts live in `/load` |
| Metrics | OpenTelemetry .NET exporter, Prometheus, Grafana | Dashboards in `/ops/grafana` |
| DB metrics | postgres_exporter, `pg_stat_statements` | Enable `pg_stat_statements` from day 1 |

> Diagram: Ticket Rush local architecture · 8 components — see the source doc linked above.

Week 1 needs only k6, the API, the primary, Prometheus and Grafana; each later week adds one component, so every bottleneck shows up on its own.

**Repository layout**

```
ticket-rush/
  src/TicketRush.Api/          # endpoints, strategies, middleware
  src/TicketRush.Data/         # DbContext, migrations, SQL
  tests/TicketRush.Tests/      # unit + Testcontainers integration tests
  load/                        # k6 scripts and saved results (JSON)
  ops/                         # docker-compose.yml, nginx.conf, grafana, prometheus
  docs/                        # weekly notes, benchmark tables, final write-up
```

**Make strategies switchable by config, not by branch.** For example `Booking:Strategy = Naive | Pessimistic | Optimistic | RedisReservation`. That lets you compare strategies with the same build, the same data, and the same k6 script.

**Fix your test machine.** Pin CPU and memory limits in Compose (for example API 2 CPU / 1 GB, Postgres 2 CPU / 2 GB). Then results from different weeks are comparable.

## Domain model and schema

A booking moves a seat from `available` to `held` (10-minute hold) to `sold`. An expired hold returns the seat to `available`. Every concurrency strategy is judged on whether that transition stays correct under load.

| Entity | Purpose | Key fields |
| --- | --- | --- |
| Event | A concert | id, name, venue, starts_at, sale_opens_at |
| Seat | One sellable seat | id, event_id, section, row, number, price_cents, status, held_by, held_until, version |
| Booking | A user's hold or purchase of 1–4 seats | id, event_id, user_id, status, created_at, expires_at, idempotency_key |
| BookingSeat | Join table | booking_id, seat_id |
| IdempotencyRecord | Stored response for a retried request (week 6) | key, user_id, request_hash, status_code, response_body, created_at |

```sql
CREATE TABLE events (
  id            BIGSERIAL PRIMARY KEY,
  name          TEXT NOT NULL,
  venue         TEXT NOT NULL,
  starts_at     TIMESTAMPTZ NOT NULL,
  sale_opens_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE seats (
  id          BIGSERIAL PRIMARY KEY,
  event_id    BIGINT NOT NULL REFERENCES events(id),
  section     TEXT NOT NULL,
  row_label   TEXT NOT NULL,
  number      INT  NOT NULL,
  price_cents INT  NOT NULL,
  status      TEXT NOT NULL DEFAULT 'available'
              CHECK (status IN ('available','held','sold')),
  held_by     BIGINT,            -- booking id
  held_until  TIMESTAMPTZ,
  version     INT NOT NULL DEFAULT 0,  -- optimistic concurrency token
  UNIQUE (event_id, section, row_label, number)
);

CREATE TABLE bookings (
  id              BIGSERIAL PRIMARY KEY,
  event_id        BIGINT NOT NULL REFERENCES events(id),
  user_id         BIGINT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('held','confirmed','expired','cancelled')),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at      TIMESTAMPTZ NOT NULL,
  idempotency_key TEXT
);

CREATE TABLE booking_seats (
  booking_id BIGINT NOT NULL REFERENCES bookings(id),
  seat_id    BIGINT NOT NULL REFERENCES seats(id),
  PRIMARY KEY (booking_id, seat_id)
);
```

**Leave indexes out on purpose** beyond primary keys and the unique constraint. You add them in week 5 and measure the difference.

**Seed data:** 1 hot event with 500 seats, plus 200 cold events with 2,000 seats each (400,000 seats in total) so queries hit a realistically sized table. Write the seeder as a CLI command (`dotnet run -- seed`) so every experiment starts from the same state.

## API contract

Six endpoints cover the whole flow. Every request carries `X-User-Id: <int>` in place of real auth. Errors use RFC 9457 Problem Details (`application/problem+json`).

| Method | Path | Purpose | Success | Notable errors |
| --- | --- | --- | --- | --- |
| GET | /events/{id} | Event details | 200 | 404 |
| GET | /events/{id}/availability | Seat map with status per seat; the hottest read | 200 | 404 |
| POST | /events/{id}/holds | Hold 1–4 specific seats for 10 minutes | 201 + booking | 409 seat taken, 422 invalid, 429 rate limited |
| POST | /bookings/{id}/confirm | Turn a hold into a sale (fake payment) | 200 | 404, 409 expired, 403 not owner |
| DELETE | /bookings/{id} | Cancel a hold, free the seats | 204 | 404, 409 already sold |
| GET | /users/me/bookings | The user's bookings; used for read-your-writes checks | 200 | — |

**Hold request**

```json
POST /events/1/holds
X-User-Id: 4821
Idempotency-Key: 7f3c2a9e-1b4d-4c8e-9a10-2f6b5d7e8c01   (required from week 6)

{ "seatIds": [1201, 1202] }
```

**Hold response (201)**

```json
{
  "bookingId": 99812,
  "status": "held",
  "seatIds": [1201, 1202],
  "expiresAt": "2026-11-05T18:10:00Z",
  "totalCents": 17000
}
```

**Conflict response (409)**

```json
{
  "type": "https://ticketrush.dev/problems/seat-unavailable",
  "title": "One or more seats are no longer available",
  "status": 409,
  "unavailableSeatIds": [1202]
}
```

**Rules**

- A hold is all-or-nothing: if any requested seat is taken, none are held.
- A user may hold at most 4 seats per event at a time (return 422 beyond that).
- A background job (`IHostedService`, every 5 s) expires holds past `expires_at` and frees their seats.
- `confirm` on an expired hold returns 409 and never sells the seat.

## Invariants and correctness checks

A fast run that breaks an invariant counts as a failed run. Run these checks automatically after every k6 run (a `verify` command or a k6 `teardown()` that calls an admin endpoint).

| # | Invariant | How to check |
| --- | --- | --- |
| I1 | No seat is sold or held twice | Query A returns 0 rows |
| I2 | Seats sold never exceed capacity | Count of `sold` seats ≤ 500 |
| I3 | Seat status agrees with bookings | Query B returns 0 rows |
| I4 | A successful hold response means the seat really is held by that booking | k6 records every 201; compare with the DB afterwards |
| I5 | No user holds more than 4 seats per event | Query C returns 0 rows |
| I6 | Retrying with the same idempotency key creates exactly 1 booking (week 6) | Count of bookings per key = 1 |

```sql
-- A: a seat linked to more than one live booking
SELECT bs.seat_id, count(*)
FROM booking_seats bs
JOIN bookings b ON b.id = bs.booking_id
WHERE b.status IN ('held','confirmed')
GROUP BY bs.seat_id
HAVING count(*) > 1;

-- B: seat status disagrees with its booking
SELECT s.id, s.status, b.status AS booking_status
FROM seats s
JOIN bookings b ON b.id = s.held_by
WHERE (s.status = 'sold' AND b.status <> 'confirmed')
   OR (s.status = 'held' AND b.status <> 'held');

-- C: users over the per-event limit
SELECT b.user_id, b.event_id, count(*)
FROM bookings b
JOIN booking_seats bs ON bs.booking_id = b.id
WHERE b.status IN ('held','confirmed')
GROUP BY b.user_id, b.event_id
HAVING count(*) > 4;
```

**Also write a deterministic concurrency test** in xUnit with Testcontainers. Fire 200 parallel `Task`s at the same 2 seats and assert exactly 1 success and 199 conflicts. It catches regressions in seconds, without a full load test.

## Load-test scenarios (k6)

Four scenarios, each a separate script, cover every experiment in Block 1. Start small (for example 1,000 users) and scale to 10,000 only once your machine stays stable. Report the size you actually ran.

| Scenario | Shape | What it exposes | Used in weeks |
| --- | --- | --- | --- |
| S1 Flash sale | 10,000 VUs ramp in 10 s; each loads availability, then holds 1–2 random seats from the same 500 | Contention, overselling, lock waits | 1, 2, 5, 6 |
| S2 Browse storm | 2,000 req/s constant-arrival on `/availability` for 2 min, 95% hot event | Read load, cache value, stampedes | 3, 4 |
| S3 Hold then check | Each VU holds a seat, then immediately calls `/users/me/bookings` | Stale reads from the replica | 4 |
| S4 Retry storm | Clients retry holds on timeout with the same idempotency key; some send 50 req/s | Duplicate bookings, abusive clients | 6 |

**S1 starter script**

```javascript
import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

const holds201 = new Counter('holds_201');
const holds409 = new Counter('holds_409');
const BASE = __ENV.BASE_URL || 'http://localhost:8080';

export const options = {
  scenarios: {
    flash_sale: {
      executor: 'ramping-vus',
      stages: [
        { duration: '10s', target: 10000 },
        { duration: '50s', target: 10000 },
      ],
    },
  },
  thresholds: {
    'http_req_duration{name:hold}': ['p(99)<1000'],
    'http_req_failed{name:hold}': ['rate<0.01'], // 409s are expected, see below
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
```

**Record for every run** (save the JSON with `k6 run --summary-export load/results/<week>-<strategy>.json`):

- Throughput (req/s) and p50, p95, p99 latency per endpoint
- 201 / 409 / 429 / 5xx counts, and the invariant check result
- Postgres: active connections, lock waits, deadlocks, and the top 5 queries from `pg_stat_statements`
- API: CPU, memory, thread-pool queue length, connection-pool waits
- Redis (when used): ops/s, hit rate, memory

## Week-by-week milestones

Each week ends with a dated note in `/docs` covering what you changed, the numbers before and after, and what surprised you. Tick off a week only when every acceptance criterion holds.

### Week 1 — Naive baseline: make it work, then watch it break

- [ ] Compose stack up: API, Postgres, Prometheus, Grafana; seeder working
- [ ] All 6 endpoints implemented the obvious way: read the seat, check `status == available`, update it, save (no locks, no transactions beyond EF defaults)
- [ ] Hold-expiry background job running
- [ ] Invariant queries and the 200-task concurrency test written
- [ ] Grafana dashboard with request rate, p50/p95/p99, error rate, DB connections
- [ ] Run S1 at 1,000 and then 10,000 VUs

**Acceptance:** you can show overselling with numbers (for example "612 successful holds for 500 seats; 37 seats held twice"). Write down why it happens: the gap between the read and the write is a race.

### Week 2 — Three ways to stop overselling

Implement each strategy behind the `Booking:Strategy` switch and run S1 against each with identical settings.

| Strategy | Mechanism | Watch for |
| --- | --- | --- |
| Pessimistic | Transaction + `SELECT … FROM seats WHERE id = ANY(@ids) ORDER BY id FOR UPDATE` | Lock waits; deadlocks if you don't lock in a fixed order |
| Pessimistic, no wait | Same with `FOR UPDATE SKIP LOCKED` or `NOWAIT`; return 409 immediately | Lower latency, more 409s |
| Optimistic | EF Core `[ConcurrencyCheck]` on `version`; `UPDATE … WHERE id = @id AND version = @v`; catch `DbUpdateConcurrencyException` | Wasted work and retries at high contention |
| Conditional update | One statement: `UPDATE seats SET status='held' … WHERE id = ANY(@ids) AND status='available' RETURNING id` | Simplest correct option; check that the row count equals the number of seats requested |
| Redis reservation | `SET seat:{id} {bookingId} NX PX 600000` per seat (Lua script for all-or-nothing), then write to Postgres | Redis and DB drifting apart; what happens if the API crashes between the two writes |

- [ ] All strategies pass I1–I5 at 10,000 VUs
- [ ] Comparison table filled in (see the benchmark template)
- [ ] Deliberately cause a deadlock (lock seats in random order), capture it in the Postgres logs, then fix it

**Acceptance:** zero invariant violations for every strategy, and a one-paragraph recommendation backed by your numbers.

### Week 3 — Caching the availability map

- [ ] Run S2 with no cache; record DB CPU and p99
- [ ] Add cache-aside on `/availability` (key `avail:{eventId}`, TTL 1 s, then 5 s, then 30 s)
- [ ] Invalidate or update the key on hold/confirm/cancel; measure how stale the map gets at each TTL
- [ ] Scale to 2 API instances behind Nginx; compare in-process `IMemoryCache` with Redis (consistency vs. latency)
- [ ] **Stampede experiment:** expire the key during S2 and graph the DB query spike
- [ ] Fix the stampede with one of: a per-key lock (`SET lock NX`), request coalescing (`Lazy<Task>` / single-flight), or early probabilistic refresh. Try .NET's `HybridCache`, which coalesces requests built in.

**Acceptance:** DB load from availability reads drops by at least 90%, no stampede spike when the key expires, and a written note on how stale the map may be and why that's acceptable (the hold endpoint is still the source of truth).

### Week 4 — Read replica and replication lag

- [ ] Add a streaming replica in Compose; route `GET` queries to it with a second `DbContext` or connection string
- [ ] Add artificial lag (`tc qdisc add … delay 200ms` on the replica's network, or `recovery_min_apply_delay = '500ms'`)
- [ ] Run S3 and count how often a user doesn't see their own hold
- [ ] Fix read-your-writes with one approach, and write down the trade-offs of the others: (a) read from the primary for N seconds after a user writes (sticky flag in a cookie or Redis), (b) wait for the replica to reach the user's last-write LSN (`pg_current_wal_lsn()` / `pg_last_wal_replay_lsn()`), (c) always read the user's own data from the primary

**Acceptance:** S3 shows 0 missing-own-booking reads with the fix enabled, replication lag is graphed in Grafana, and you can explain why the availability map may still come from the lagging replica.

### Week 5 — Indexes, query plans and connection pools

- [ ] Pull the top 5 queries from `pg_stat_statements` after an S1 + S2 run
- [ ] `EXPLAIN (ANALYZE, BUFFERS)` each one; save the plans in `/docs/plans`
- [ ] Add indexes and measure each: `seats(event_id, status)`, a partial index `seats(held_until) WHERE status = 'held'` for the expiry job, `bookings(user_id, event_id)`, `bookings(idempotency_key)` unique
- [ ] Compare EF Core with Dapper on the hold path (allocations with `dotnet-counters`, latency)
- [ ] **Pool experiment:** set `Maximum Pool Size` to 5, 20, 100 and 300 and run S1 each time. Find where throughput peaks and where it collapses. Then put PgBouncer (transaction mode) in front and repeat.
- [ ] Look for a sync-over-async or long-held transaction with `dotnet-trace` or `pg_stat_activity`

**Acceptance:** before/after plans for every index added, a graph or table showing throughput by pool size, and a written rule of thumb for sizing pools on this machine.

### Week 6 — Idempotency and rate limiting

- [ ] Require `Idempotency-Key` on `POST /holds` and `/confirm`
- [ ] Middleware: on first request, insert an `IdempotencyRecord` in the same transaction as the booking; on retry with the same key and the same request hash, return the stored response; with a different hash, return 422
- [ ] Handle the race where two identical requests arrive together (unique constraint plus a 409 or wait for the first to finish)
- [ ] Rate limiting step 1: ASP.NET Core `AddRateLimiter` with a token bucket per `X-User-Id` (for example 5 holds per 10 s); observe how it breaks with 2 instances
- [ ] Rate limiting step 2: a distributed limiter in Redis (sliding window with a sorted set, or a token bucket in a Lua script); return `429` with a `Retry-After` header
- [ ] Run S4: retries with jitter vs. without; one abusive client at 50 req/s

**Acceptance:** I6 holds under S4 (exactly 1 booking per key), the abusive client is limited across both instances while normal users' p99 stays within 10% of the S1 baseline, and limiter overhead per request is measured.

## Benchmark and write-up templates

Fill the table in as you run (scenario S1, same VUs, same machine). The empty cells are yours to fill in.

| Strategy | VUs | Hold req/s | p50 (ms) | p95 (ms) | p99 (ms) | 201s | 409s | 5xx | Invariants | DB lock waits | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Naive | 10,000 |  |  |  |  |  |  |  |  |  |  |
| Pessimistic (FOR UPDATE) | 10,000 |  |  |  |  |  |  |  |  |  |  |
| Pessimistic (SKIP LOCKED) | 10,000 |  |  |  |  |  |  |  |  |  |  |
| Optimistic (version) | 10,000 |  |  |  |  |  |  |  |  |  |  |
| Conditional UPDATE | 10,000 |  |  |  |  |  |  |  |  |  |  |
| Redis reservation | 10,000 |  |  |  |  |  |  |  |  |  |  |

**Final write-up (2 pages, in `/docs/block1-writeup.md`)**

```markdown
# Ticket Rush: concurrency control under a flash sale

## 1. Problem
10,000 users, 500 seats, 60 seconds. Invariants I1–I6.

## 2. Setup
Hardware limits, versions, data size, k6 scenario, how invariants were checked.

## 3. Results
The benchmark table + 1–2 Grafana screenshots (p99 and DB lock waits).

## 4. Analysis
Why each strategy behaved as it did. Where the bottleneck moved after each fix.

## 5. Recommendation
Which strategy you would ship, at what scale it stops working,
and what you would change at 10x (sharding by event? queue-based admission?).

## 6. What surprised me
3 bullets.
```

Publish the repo with the write-up as its README highlight. It's a strong portfolio piece and a ready-made story for interviews.

## Stretch goals and pitfalls

**Stretch goals** (only after week 6 is done)

- **Virtual waiting room:** admit users from a Redis queue at a fixed rate (for example 200/s) instead of letting 10,000 hit the hold endpoint at once. Compare p99 and fairness.
- **Seat sharding by section:** partition the hot event's seats across lock domains and measure whether contention drops.
- **Hot-key mitigation:** split `avail:{eventId}` into per-section keys and update them with a diff instead of rebuilding the whole map.
- **Server-Sent Events:** push seat-status changes to browsers instead of polling `/availability`.
- **Postgres isolation levels:** run the naive strategy under `SERIALIZABLE` and explain the serialization failures you get.

**Pitfalls to avoid**

- **Load generator as bottleneck:** k6 on the same machine competes for CPU. Watch k6's own CPU, or run it on a second machine or container with its own limits.
- **Measuring the wrong thing:** look at p99 and error counts, not averages. A run that oversells "fast" counts as a failure.
- **Warm vs. cold:** do one warm-up run and discard it; reset data with the seeder before every measured run.
- **Hidden retries:** `HttpClient`, Polly, or EF's `EnableRetryOnFailure` can hide conflicts and inflate latency. Know what's on.
- **Clock assumptions:** use the database's `now()` for hold expiry, not each API instance's clock.
- **Changing two things at once:** one variable per run, or the comparison means nothing.
