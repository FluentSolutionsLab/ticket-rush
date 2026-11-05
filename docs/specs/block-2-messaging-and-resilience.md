# Ticket Rush — Block 2 Spec

_Spec as of 2026-11-05. Source: https://claude.ai/artifact/8amRgHs4PaeVCBKDdgFKny_

## Overview

Block 2 splits Ticket Rush into a .NET booking service and NestJS payment and notification services that talk through a message broker. Over weeks 7–12 you make that system correct when messages are duplicated, delayed or lost, and observable when it fails.

**By the end of Block 2 you can:**

- Explain at-least-once delivery and design consumers that are safe to run twice.
- Use the transactional outbox so a database write and its event can never diverge.
- Build a booking saga two ways, orchestration and choreography, with compensation and timeouts.
- Make calls resilient with timeouts, retries with jitter, circuit breakers and bulkheads, and show what each one prevents.
- Trace one request across .NET, Node and the broker, and run a chaos day with written postmortems.

**Carried over from Block 1:** the booking service, its Postgres schema, the strategy you picked in week 2 (keep it fixed now), the Redis cache and rate limiter, the k6 scripts, and the invariant checks I1–I6.

**In scope:** 3 services + an API gateway, RabbitMQ (weeks 7–10), Kafka (week 7 comparison and stretch), a fake payment provider, and full local observability.

**Out of scope (Block 3+):** real payments, cloud deployment, Kubernetes, multi-region, and consensus.

## Target architecture

By week 12 the system is a gateway, three services and one broker. Booking never calls Payment or Notification directly: a hold becomes an event, and the saga moves forward on the replies.

> Diagram: Block 2 target architecture · 3 services, 1 broker — see the source doc linked above.

The only synchronous calls left are client → gateway → booking and payment → provider, which is where the week 10 resilience patterns go.

## Services and data ownership

Each service owns its own database, and no service reads another's tables. Everything crosses a boundary as an HTTP call through the gateway or as a message on the broker.

| Service | Stack | Owns | Database | Talks to |
| --- | --- | --- | --- | --- |
| Gateway | .NET, YARP | Routing, rate limiting (moved from Block 1), correlation IDs | — | Booking (HTTP) |
| Booking | ASP.NET Core, EF Core, MassTransit | Events, seats, bookings, the saga state (orchestration variant) | Postgres `booking` | Broker; Redis |
| Payment | NestJS, `@nestjs/microservices`, TypeORM or Prisma | Payment attempts, refunds, the fake provider client | Postgres `payment` | Broker; Fake provider (HTTP) |
| Notification | NestJS, BullMQ | Email/SMS outbox, templates, delivery attempts | Postgres `notification` + Redis (BullMQ) | Broker |
| Fake payment provider | Small Node or .NET app | Simulated card charges with configurable latency, failure rate and timeouts | In-memory | — |

**One Postgres server, three databases** is fine locally. Separate databases still enforce the rule that services share data only through messages.

**Fake provider knobs** (environment variables, changeable at runtime through `POST /admin/config`):

- `LATENCY_MS` (default 200) and `LATENCY_JITTER_MS` (default 100)
- `FAILURE_RATE` (default 0.05): returns `402 card_declined`
- `ERROR_RATE` (default 0.0): returns `500`
- `TIMEOUT_RATE` (default 0.0): hangs for 30 s
- `CHARGE_THEN_FAIL_RATE` (default 0.0): charges the card, then returns `500`. This is the nastiest case and the main reason you need idempotency keys towards the provider.

**Repository layout**

```
ticket-rush/
  src/Gateway/                 # YARP
  src/Booking/                 # from Block 1, plus MassTransit
  src/Contracts/               # shared message contracts (C#), source of the JSON schemas
  services/payment/            # NestJS
  services/notification/       # NestJS + BullMQ
  services/fake-provider/
  contracts/schemas/           # JSON Schema per message, used by the Node services
  src/TicketRush.AppHost/      # .NET Aspire orchestration (week 11)
  load/  chaos/  docs/
```

## Event and command contracts

Commands ask one service to do something and are named in the imperative. Events state something that already happened and are named in the past tense. Every message uses the same envelope, so tracing, deduplication and versioning work the same in C# and TypeScript.

**Envelope**

```json
{
  "messageId": "0b6c1f3e-6a2d-4f7e-9d1a-3c5e8b7a9f20",
  "type": "ticketrush.booking.SeatsHeld",
  "version": 1,
  "occurredAt": "2026-11-05T18:00:00.123Z",
  "correlationId": "booking-99812",
  "causationId": "5e7d9c1b-...",
  "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
  "payload": { }
}
```

- `messageId` is the deduplication key for consumers.
- `correlationId` is the booking ID for the whole saga. `causationId` is the `messageId` of the message that caused this one.
- `traceparent` carries the W3C trace context so one trace spans every hop (week 11).

**Messages**

| Message | Kind | Producer | Consumers | Payload |
| --- | --- | --- | --- | --- |
| SeatsHeld | Event | Booking | Payment, Notification | bookingId, userId, eventId, seatIds, amountCents, expiresAt |
| ChargePayment | Command | Booking (orchestration only) | Payment | bookingId, userId, amountCents, idempotencyKey |
| PaymentSucceeded | Event | Payment | Booking, Notification | bookingId, paymentId, amountCents, providerRef |
| PaymentFailed | Event | Payment | Booking, Notification | bookingId, reason (`declined`, `provider_error`, `timeout`) |
| BookingConfirmed | Event | Booking | Notification | bookingId, userId, seatIds |
| BookingCancelled | Event | Booking | Payment, Notification | bookingId, reason (`payment_failed`, `hold_expired`, `user_cancelled`) |
| RefundPayment | Command | Booking (orchestration only) | Payment | bookingId, paymentId, amountCents |
| PaymentRefunded | Event | Payment | Booking, Notification | bookingId, paymentId |
| SendNotification | Command (internal) | Notification | Notification worker (BullMQ) | userId, template, data |

**Versioning rules**

1. Adding an optional field is not a breaking change; keep `version` the same.
2. Renaming, removing or changing the meaning of a field means a new `version`. Publish both versions until every consumer has moved over.
3. Consumers ignore fields they don't know.
4. Contracts live in `src/Contracts` (C# records). Generate JSON Schemas from them (for example with NJsonSchema), and validate incoming messages in NestJS with Ajv or Zod built from those schemas. A contract test in CI fails if the two sides drift apart.

**Topology (RabbitMQ):** one topic exchange per producing service (`booking.events`, `payment.events`), one queue per consumer (`payment.on-seats-held`), routing key = message type. Every queue has a dead-letter exchange and a `*.dlq` queue.

## Booking saga

The saga's job is to make sure no booking ends charged but unsold, or sold but unpaid. Build it once as an orchestrator (week 9, a MassTransit state machine in Booking) and once as choreography (each service reacts to events). The states and transitions are the same in both.

> Diagram: booking saga · 6 states, 3 final — see the source doc linked above.

The tricky transition is the late payment: the hold expired and the seats were released, then `PaymentSucceeded` arrives. The saga must refund, never confirm.

| Rule | Value |
| --- | --- |
| Hold timeout | 10 min (scheduled message from the saga, or the Block 1 expiry job emitting `BookingCancelled`) |
| Saga correlation | `bookingId` |
| Saga persistence | EF Core table in the booking DB, with optimistic concurrency on the saga row |
| Duplicate or out-of-order events | Ignored once a final state is reached; logged with the current state |
| Payment retries | Inside the Payment service only; the saga sees one result per charge |

## Delivery guarantees and invariants

The broker delivers at least once, so every consumer must be idempotent. Every producer must write its state change and its outgoing message in one local transaction (the outbox). Together those give you effectively-once processing without distributed transactions.

**Outbox (producer side)**

```sql
CREATE TABLE outbox_messages (
  id            UUID PRIMARY KEY,        -- becomes messageId
  type          TEXT NOT NULL,
  payload       JSONB NOT NULL,
  headers       JSONB NOT NULL,          -- correlationId, traceparent...
  occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  dispatched_at TIMESTAMPTZ
);
CREATE INDEX ON outbox_messages (occurred_at) WHERE dispatched_at IS NULL;
```

- **Booking (.NET):** first write it yourself (a relay `BackgroundService` polling with `FOR UPDATE SKIP LOCKED`), then switch to MassTransit's built-in EF Core outbox and compare.
- **Payment (NestJS):** write the outbox row in the same TypeORM/Prisma transaction, plus a small relay worker.

**Inbox (consumer side)**

```sql
CREATE TABLE inbox_messages (
  message_id   UUID NOT NULL,
  consumer     TEXT NOT NULL,
  processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (message_id, consumer)
);
```

Insert the inbox row and apply the side effect in one transaction. On a unique-key violation, acknowledge the message and skip it: it's a duplicate.

**Invariants** (checked after every load or chaos run, in addition to Block 1's I1–I6)

| # | Invariant | How to check |
| --- | --- | --- |
| M1 | No booking is charged twice | Payment DB: `count(*)` of succeeded charges per bookingId ≤ 1, and the fake provider's charge log agrees |
| M2 | Every confirmed booking has exactly 1 succeeded payment | Cross-DB check script joining booking and payment data by bookingId |
| M3 | Every cancelled booking that was charged has been refunded | Charged and cancelled bookings without `PaymentRefunded` = 0 |
| M4 | No saga is stuck | Sagas not in a final state and older than hold TTL + 2 min = 0 |
| M5 | The outbox drains | `dispatched_at IS NULL` rows older than 30 s = 0 once load stops |
| M6 | No user gets duplicate notifications | Notification DB: sends per (bookingId, template) ≤ 1 |
| M7 | Dead-letter queues are explained | Every message in a DLQ has a known cause written in the run notes |

Write the cross-service checks as one `verify` script (Node or .NET) that reads all three databases and the fake provider's log, then prints a pass/fail table.

## Load and fault-injection scenarios

Every experiment combines a steady k6 load with one injected fault, then runs the M1–M7 checks. Put Toxiproxy between each service and its dependencies (Postgres, RabbitMQ, the fake provider) from week 10, so faults can be switched on and off through its API without restarts.

**Baseline load (L1):** 200 bookings per second for 5 minutes. Each k6 VU holds seats, then polls `GET /bookings/{id}` until the status is `confirmed` or `cancelled` (timeout 30 s). Record end-to-end saga time as a custom k6 `Trend`.

| ID | Fault | How to inject | Expected behaviour |
| --- | --- | --- | --- |
| F1 | Duplicate delivery | Consumer acks only after a random 30% of messages are processed twice (crash after the side effect, before the ack) | Inbox dedupes; M1 and M6 hold |
| F2 | Poison message | Publish a `SeatsHeld` with an invalid payload | Retried N times, then lands in the DLQ; the rest of the queue keeps flowing |
| F3 | Slow provider | `LATENCY_MS=3000` | Timeouts fire; the circuit breaker opens; bookings fail fast with `PaymentFailed(timeout)` |
| F4 | Flaky provider | `ERROR_RATE=0.3` | Retries with jitter recover most payments; no double charge |
| F5 | Charge-then-fail | `CHARGE_THEN_FAIL_RATE=0.1` | The retry uses the same provider idempotency key, so the card is charged once (M1) |
| F6 | Broker down | `docker compose stop rabbitmq` for 60 s, then start | Outbox rows pile up, then drain; no message lost (M5) |
| F7 | Payment service crash | Kill the NestJS container mid-load | Unacked messages are redelivered; sagas finish after restart (M4) |
| F8 | Database latency | Toxiproxy `latency` 500 ms on the payment DB | The bulkhead keeps booking's HTTP endpoints responsive |
| F9 | Consumer lag | Scale payment consumers to 1 with prefetch 1 | Queue depth grows; holds expire; compensation runs (M3, M4) |
| F10 | Network partition | Toxiproxy `timeout` toxic between booking and RabbitMQ | Publishes buffer in the outbox; the publisher reconnects |

**Record for every run:** end-to-end saga p50/p95/p99, queue depth and consumer lag over time, DLQ counts, retry counts, circuit-breaker state changes, outbox backlog, and the M1–M7 results.

## Week-by-week milestones

As in Block 1, each week ends with a dated note in `/docs` and is ticked off only when every acceptance criterion holds.

### Week 7 — Split the services and start messaging

- [ ] Add the YARP gateway; move rate limiting into it
- [ ] Scaffold the Payment and Notification NestJS services, each with its own database
- [ ] Booking publishes `SeatsHeld` with MassTransit on RabbitMQ (publish directly for now, no outbox yet)
- [ ] Payment consumes it with the NestJS RabbitMQ transport (or `@golevelup/nestjs-rabbitmq` for topic exchanges), calls the fake provider, publishes `PaymentSucceeded`/`PaymentFailed`
- [ ] Booking consumes the result and confirms or cancels
- [ ] Contract test: C# records and JSON Schemas agree
- [ ] **Kafka comparison:** run the same `SeatsHeld` flow through Kafka (Confluent.Kafka in .NET, KafkaJS in Node) with the booking ID as the partition key. Note what changes: ordering per key, consumer groups, offsets vs. acks, replay.

**Acceptance:** a booking goes from hold to confirmed through the broker at L1 load; you can explain in one paragraph when you'd pick RabbitMQ over Kafka for this system and why.

### Week 8 — Outbox, inbox and idempotent consumers

- [ ] First show the dual-write bug: kill booking after the DB commit but before the publish, and count lost events
- [ ] Write the outbox relay by hand; then switch to MassTransit's EF Core outbox and compare backlog and latency
- [ ] Outbox in the Payment service (NestJS)
- [ ] Inbox table in every consumer; run F1 (duplicate delivery)
- [ ] Provider idempotency key = `bookingId`; run F5 (charge-then-fail)
- [ ] Retry policy + DLQ on every queue; run F2 (poison message)

**Acceptance:** F1, F2, F5 and F6 all pass M1, M5 and M6. You have a before/after count of lost events for the dual-write bug.

### Week 9 — The booking saga, two ways

- [ ] **Orchestration:** a MassTransit state machine saga in Booking, persisted with EF Core, that sends `ChargePayment` and `RefundPayment` commands and schedules a hold-expiry timeout
- [ ] **Choreography:** turn the orchestrator off; each service reacts only to events (`SeatsHeld` → Payment charges; `PaymentFailed` → Booking releases seats; `BookingCancelled` after a charge → Payment refunds)
- [ ] Handle the race: a payment succeeds after the hold already expired. The saga must refund and must not confirm.
- [ ] Make both variants switchable by config; run L1 + F9 (consumer lag) against each

**Acceptance:** M2–M4 hold for both variants. Your note compares them on readability, coupling, how easy each was to debug, and where the business rules ended up.

### Week 10 — Resilience patterns

- [ ] Put Toxiproxy in front of the fake provider, RabbitMQ and the payment DB
- [ ] Payment → provider: timeout (2 s), retries with exponential backoff + jitter (max 3), circuit breaker. Use `opossum` or `cockatiel` in NestJS.
- [ ] Gateway → Booking: Polly v8 via `Microsoft.Extensions.Http.Resilience` (standard resilience handler), then tune it by hand
- [ ] Bulkhead: separate concurrency limits for the hold endpoint and status polling, so slow payments can't starve holds
- [ ] Run F3, F4 and F8, each with resilience off and then on
- [ ] Demonstrate a retry storm: retries without jitter or a budget against a provider at 30% errors; graph the amplification

**Acceptance:** for each pattern you have a graph showing the failure without it and the recovery with it. Retry amplification stays under 1.5× with a retry budget.

### Week 11 — Observability end to end

- [ ] A .NET Aspire AppHost starts everything (the .NET projects, the Node services via `AddNpmApp`/`AddNodeApp`, Postgres, Redis, RabbitMQ) with one command
- [ ] OpenTelemetry in .NET (ASP.NET Core, HttpClient, EF Core, MassTransit instrumentation) and in Node (`@opentelemetry/sdk-node` with auto-instrumentations)
- [ ] Propagate `traceparent` through message headers so one trace runs from gateway to notification
- [ ] Export to Grafana (Tempo for traces, Prometheus for metrics, Loki for logs); structured logs carry `traceId` and `correlationId`
- [ ] Dashboard: saga duration, queue depth per queue, consumer lag, DLQ count, outbox backlog, breaker state
- [ ] Two alerts: DLQ count > 0, and saga p95 > 10 s
- [ ] Define 2 SLOs (for example 99% of holds answered in under 300 ms; 99% of sagas finished within 15 s)

**Acceptance:** from one failed booking ID you can find its full trace across 4 services and the broker in under 2 minutes.

### Week 12 — Chaos day and postmortems

- [ ] Write a chaos plan: 5 faults from the scenarios table, plus 1 you haven't tried, each with a hypothesis written before you run it
- [ ] Run each under L1 load, with dashboards recording
- [ ] Write a blameless postmortem for every fault where a hypothesis was wrong or an invariant broke
- [ ] Fix the top 2 issues and rerun those faults

**Acceptance:** at least 3 postmortems, both fixes verified by a rerun, and M1–M7 green on the final run of every fault.

## Deliverables and postmortem template

Block 2 ends with four artifacts in `/docs`, linked from the repo README.

- [ ] **Architecture diagram** of the final system, including queues and the DLQs
- [ ] **Saga comparison** (1 page): orchestration vs. choreography, with your recommendation
- [ ] **Resilience report**: one before/after graph per pattern, plus the retry-storm graph
- [ ] **Postmortems**: at least 3, using the template below, and a screenshot of the main dashboard

**Fault results table** (fill in during week 12)

| Fault | Hypothesis | Saga p99 (s) | Lost messages | Double charges | DLQ count | Invariants | Outcome |
| --- | --- | --- | --- | --- | --- | --- | --- |
| F3 Slow provider |  |  |  |  |  |  |  |
| F5 Charge-then-fail |  |  |  |  |  |  |  |
| F6 Broker down |  |  |  |  |  |  |  |
| F7 Payment crash |  |  |  |  |  |  |  |
| F9 Consumer lag |  |  |  |  |  |  |  |
| Your own fault |  |  |  |  |  |  |  |

**Postmortem template**

```markdown
# Postmortem: <short title>

**Date:** YYYY-MM-DD  **Severity:** (what would this be in production?)

## Summary
2–3 sentences: what broke, for how long, who it would have affected.

## Impact
Numbers: failed bookings, double charges, saga p99, messages in DLQ.

## Timeline
| Time | Event |
| --- | --- |
| 14:02:10 | Fault injected (Toxiproxy latency 3 s on provider) |
| 14:02:40 | Circuit breaker opened |
| ... | ... |

## Root cause
The mechanism, not "the provider was slow". Use 5 whys.

## What went well / what went badly

## Detection
Which alert or graph showed it first? How long until you noticed?

## Action items
| Action | Type (prevent / detect / mitigate) | Done |
| --- | --- | --- |
```

## Stretch goals and pitfalls

**Stretch goals** (only after week 12 is done)

- **Kafka all the way:** move the event flow to Kafka with the outbox relayed by Debezium (change data capture) instead of a polling relay. Compare latency and operational effort.
- **Event replay:** rebuild a read model (for example, sales per event) by replaying Kafka topics from offset 0.
- **Schema registry:** put Avro or Protobuf schemas in a registry and enforce compatibility in CI.
- **Waiting room as a queue:** admit users into the hold endpoint from a queue at a fixed rate and compare with the rate limiter.
- **Temporal or Dapr workflows:** rebuild the saga on a workflow engine and compare with the MassTransit state machine.

**Pitfalls to avoid**

- **Distributed monolith:** if one service can't deploy or start without another, the boundaries are wrong. Services should start and queue work even when a peer is down.
- **Shared database by the back door:** a "quick" read of another service's tables breaks ownership. Use an event or an API.
- **Acking too early:** acknowledge a message only after its transaction commits, or a crash loses it.
- **Retries without idempotency:** every retry on a non-idempotent call is a potential double charge. Add the idempotency key first, then the retry.
- **Infinite redelivery:** without a retry limit and a DLQ, one poison message blocks a queue forever.
- **Assuming order:** RabbitMQ with several consumers doesn't preserve order, and Kafka only does so per partition. Make handlers tolerate `PaymentSucceeded` arriving after `BookingCancelled`.
- **Timeouts that don't add up:** a gateway timeout shorter than the downstream timeout plus its retries guarantees work that finishes after the client has given up. Set timeout budgets from the outside in.
- **Over-reading the numbers:** a laptop with 10 containers is a lab, not production. Compare runs with each other, and don't quote absolute throughput as a capacity.
