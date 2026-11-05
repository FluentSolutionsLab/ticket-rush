# Load tests (k6)

| Script | Scenario | Weeks |
| --- | --- | --- |
| `s1-flash-sale.js` | S1 Flash sale | 1, 2, 5, 6 |
| `s2-browse-storm.js` | S2 Browse storm (to write in week 3) | 3, 4 |
| `s3-hold-then-check.js` | S3 Hold then check (to write in week 4) | 4 |
| `s4-retry-storm.js` | S4 Retry storm (to write in week 6) | 6 |

Save every measured run as `results/<week>-<strategy>.json` with `--summary-export`. Raw JSON is
git-ignored; copy the numbers that matter into `docs/benchmarks/`.

Before every measured run: reset data (`docker compose -f ops/docker-compose.yml down -v`, then up and seed),
do one warm-up run and discard it, and change only one variable at a time.
