# Local stack

```bash
docker compose -f ops/docker-compose.yml up -d --build
```

| Service | URL |
| --- | --- |
| API | http://localhost:8080/health, http://localhost:8080/metrics |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (anonymous admin, local only) |
| Postgres | localhost:5432, database/user/password `ticketrush` |

Switch the booking strategy without rebuilding:

```bash
BOOKING_STRATEGY=Pessimistic docker compose -f ops/docker-compose.yml up -d api
```

Reset all data (do this before every measured run):

```bash
docker compose -f ops/docker-compose.yml down -v
```
