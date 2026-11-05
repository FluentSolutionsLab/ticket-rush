# Ticket Rush

A concert-ticket booking system where **10,000 users fight for 500 seats in 60 seconds**, built
four times over 26 weeks to practise system design: a monolith, event-driven services, a
consensus-based store, and cloud-native deployments on AWS and Azure.

No overselling. No double charges. Every claim backed by a measurement.

## The plan

| Block | Weeks | Focus | Spec |
| --- | --- | --- | --- |
| 1 | 1–6 | Data layer under load: concurrency control, caching, replication, indexes, idempotency, rate limiting | [Block 1](docs/specs/block-1-data-and-performance.md) |
| 2 | 7–12 | Event-driven services: messaging, outbox, sagas, resilience, observability, chaos | [Block 2](docs/specs/block-2-messaging-and-resilience.md) |
| 3 | 13–18 | Distributed systems: Raft, linearizability, quorums, actors | [Block 3](docs/specs/block-3-distributed-systems.md) |
| 4 | 19–26 | Cloud architecture on AWS and Azure: managed services, Terraform, DR, cost | [Block 4](docs/specs/block-4-cloud-architecture.md) |

Progress is tracked with one GitHub milestone per week, one issue per checklist item, and one
pull request per week that includes that week's note in [`docs/weekly/`](docs/weekly/).

## Evolution

Each block ends with a git tag, so every version stays browsable.

| Version | Architecture | Key lesson | Tag |
| --- | --- | --- | --- |
| 1 | Monolith + Postgres | Concurrency control choices, with benchmarks | `block-1-monolith` (week 6) |
| 2 | Event-driven services + saga | Outbox, idempotency, resilience, postmortems | `block-2-event-driven` (week 12) |
| 3 | Raft + KV store (write-up only) | Consensus and linearizability from first principles | `block-3-distributed` (week 18) |
| 4 | Cloud-native on AWS and Azure | Managed-service trade-offs, porting, DR, cost | `block-4-cloud` (week 26) |

## Run it locally

Requirements: .NET 10 SDK, Docker, [k6](https://k6.io/).

```bash
docker compose -f ops/docker-compose.yml up -d --build
curl localhost:8080/health
```

```bash
dotnet test
```

Integration tests start a throwaway Postgres with Testcontainers, so Docker must be running.

| Service | URL |
| --- | --- |
| API | http://localhost:8080 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 |

## Repository layout

```
src/TicketRush.Api/     endpoints, strategies, middleware (Block 1)
src/TicketRush.Data/    data access and SQL schema
tests/                  unit + Testcontainers integration tests
load/                   k6 scenarios
ops/                    docker-compose, Prometheus, Grafana
docs/                   specs, weekly notes, benchmarks, ADRs, postmortems
```

Later blocks add `services/` (NestJS), `experiments/` (Block 3 quorum and Orleans work) and
`infra/aws`, `infra/azure` (Terraform).

## Results in numbers

_Filled in as the blocks complete: throughput, p99, failover windows, RPO/RTO, cost per booking._
