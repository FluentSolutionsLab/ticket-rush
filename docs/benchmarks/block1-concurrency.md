# Block 1: concurrency strategies under S1

Scenario S1, same VUs, same machine, same resource limits. Fill in as you run.

| Strategy | VUs | Hold req/s | p50 (ms) | p95 (ms) | p99 (ms) | 201s | 409s | 5xx | Invariants | DB lock waits | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Naive | | | | | | | | | | | |
| Pessimistic (FOR UPDATE) | | | | | | | | | | | |
| Pessimistic (SKIP LOCKED) | | | | | | | | | | | |
| Optimistic (version) | | | | | | | | | | | |
| Conditional UPDATE | | | | | | | | | | | |
| Redis reservation | | | | | | | | | | | |
