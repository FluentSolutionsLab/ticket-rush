# Ticket Rush — Block 4 Spec

_Spec as of 2026-11-05. Source: https://claude.ai/artifact/7eWkRSpNEhLDb9N5dAhcY7_

## Overview

Block 4 takes Ticket Rush from Docker Compose to managed-cloud deployments on **both AWS and Azure** that you could defend in an architecture review. Over 8 weeks (19–26) you redesign it with managed services, deploy it twice with Terraform, test failure and cost on both clouds, and finish with an architect certification.

**By the end of Block 4 you can:**

- Review a system against the Well-Architected Framework's pillars and turn the gaps into a prioritised backlog.
- Choose managed services by trade-off (operational effort, consistency, scaling model, cost), not by habit.
- Model data for a key-value/document store from access patterns, including conditional writes that prevent overselling.
- Deploy repeatable environments with Terraform and a CI/CD pipeline.
- State RPO and RTO targets, pick a DR strategy that meets them, and prove it in a game day.
- Estimate the monthly bill at 1×, 10× and 100× traffic, and say which component dominates at each step.

**How it closes the plan:** Block 1 taught you the data layer, Block 2 messaging and resilience, Block 3 the theory under both. Block 4 is where those meet real constraints: money, managed-service limits, regions failing, and two vendors with different answers to the same question. Doing both clouds adds 2 weeks to the original 6-week block, so the full plan now runs about 26 weeks.

**Out of scope:** Kubernetes (a stretch goal), failover between clouds (each cloud is a separate, complete deployment), and organisation-wide landing zones.

## Cloud choice and cost guardrails

Build the same system on both clouds, **one after the other**, never in parallel. Do AWS first (week 21), then port it to Azure (week 22). The port is where most of the learning happens: every service that doesn't translate one-to-one forces a design decision you have to justify. Keep the application code cloud-neutral (ports and adapters for storage, messaging and secrets), so the port changes adapters and Terraform, not business logic.

**Set up guardrails before deploying anything (day 1 of week 19)**

- [ ] A dedicated AWS member account (AWS Organizations) and a dedicated Azure subscription for this project, never your employer's
- [ ] MFA on the AWS root user and the Azure owner account; day-to-day work through IAM Identity Center users and Entra ID users with least privilege
- [ ] Budgets with alerts at 50%, 80% and 100% of a monthly limit on each cloud (AWS Budgets, Azure Cost Management budgets), sent to your email
- [ ] The same tags on both clouds: `project=ticket-rush`, `env=dev|prod`, `cloud=aws|azure`, `owner=<you>`; cost allocation by tag enabled
- [ ] One command per environment per cloud to destroy everything (`terraform destroy`), tested in weeks 21 and 22
- [ ] A rule: never leave both clouds running at the same time outside a planned session; tear down the same day

**Keep costs down:** use the smallest instance sizes, on-demand or serverless capacity modes, and a single NAT gateway (or VPC endpoints) in dev. Check prices for your region in the [AWS Pricing Calculator](https://calculator.aws/) or the [Azure Pricing Calculator](https://azure.microsoft.com/pricing/calculator/) before each experiment, not after.

## Service mapping

Every self-hosted piece from Block 2 gets a managed replacement, and each swap changes a guarantee you relied on. The last column is what to re-check.

| Block 2 component | AWS | Azure equivalent | What changes |
| --- | --- | --- | --- |
| Nginx / YARP gateway | Application Load Balancer + API Gateway (or keep YARP on ECS) | Application Gateway or Azure Front Door + API Management (or keep YARP) | Rate limiting moves to the managed layer; check its limits and cost per request |
| Booking, Payment, Notification containers | ECS on Fargate | Azure Container Apps | No hosts to patch; scaling rules on CPU, requests or queue depth |
| Postgres | Aurora PostgreSQL or RDS PostgreSQL, Multi-AZ | Azure Database for PostgreSQL Flexible Server, zone-redundant HA | Failover takes seconds to a minute; your connection handling must survive it |
| Seat inventory (redesign) | DynamoDB | Cosmos DB (NoSQL API) | Conditional writes replace row locks; partition keys decide hot spots |
| Redis | ElastiCache for Redis / Valkey | Azure Cache for Redis | Cluster mode, eviction policy, failover behaviour |
| RabbitMQ | SNS + SQS (fan-out), or Amazon MQ for RabbitMQ | Service Bus topics + subscriptions | At-least-once still; ordering only with FIFO queues or sessions; DLQs built in |
| Kafka (stretch) | Amazon MSK | Event Hubs (Kafka API) | Partition counts and retention are now a cost line |
| Outbox relay | DynamoDB Streams → Lambda, or the existing relay on ECS | Cosmos DB change feed → Azure Functions | Change data capture replaces polling |
| Secrets in `.env` files | Secrets Manager / Parameter Store | Key Vault | Rotation, IAM-scoped access, no secrets in images |
| Prometheus, Grafana, Tempo, Loki | CloudWatch + X-Ray, or Amazon Managed Prometheus/Grafana via the OpenTelemetry Collector (ADOT) | Azure Monitor + Application Insights (OpenTelemetry distro) | Keep OpenTelemetry in code; only the exporter config changes |
| Toxiproxy chaos | AWS Fault Injection Service | Azure Chaos Studio | Real AZ and dependency failures, with guardrails |

**Decision record:** for the three rows you find least obvious, write a short ADR (context, options, decision, consequences) in `/docs/adr`.

## Target architecture

Both deployments have the same shape: one region across availability zones, a seat store with conditional writes, managed messaging, and a scaled-down second region used only for the DR drill. They differ in the services and in where each one's guarantees stop.

> Diagram: Ticket Rush on AWS · 1 region, 2 zones, warm-standby region — see the source doc linked above.

**AWS:** tasks in both zones call the regional services. Only zone b's arrows are drawn, to keep the picture readable. All writes go to the Aurora primary, and DynamoDB Streams feed a Lambda relay that publishes to SNS, replacing the polling outbox relay from Block 2.

> Diagram: Ticket Rush on Azure · 1 region, 2 zones, warm-standby region — see the source doc linked above.

**Azure:** the same shape with different services. Container Apps replicas spread across zones, and the Cosmos DB change feed triggers an Azure Function that publishes to Service Bus. The biggest design difference is in the seat store: Cosmos DB batches are atomic only within one partition (see Data design).

## Data design

Move only the seat inventory and bookings to DynamoDB (or Cosmos DB), and keep payments in Postgres. That puts the hottest, simplest access pattern on a store that scales horizontally, and leaves relational data where joins and transactions are cheap.

**Start from access patterns, not entities**

| # | Access pattern | Frequency in a flash sale |
| --- | --- | --- |
| A1 | Get the seat map for an event | Very high (cached) |
| A2 | Hold 1–4 specific seats atomically | High, heavy contention |
| A3 | Confirm or release a booking's seats | High |
| A4 | List a user's bookings | Medium |
| A5 | Find expired holds | Background, every few seconds |
| A6 | Get a booking by ID | Medium |

**Single-table design (DynamoDB)**

| Item | PK | SK | Other attributes |
| --- | --- | --- | --- |
| Event | `EVENT#<eventId>` | `META` | name, venue, startsAt |
| Seat | `EVENT#<eventId>#S<section>` | `SEAT#<row>#<number>` | status, bookingId, heldUntil, version |
| Booking | `BOOKING#<bookingId>` | `META` | userId, eventId, status, seatKeys, expiresAt |
| User's booking (GSI1) | GSI1PK `USER#<userId>` | GSI1SK `<createdAt>#<bookingId>` | projection of booking fields |

- **A1** is a `Query` per section partition. Splitting seats by section spreads a hot event over several partitions instead of one.
- **A2** is a `TransactWriteItems` with one conditional update per seat (`status = available OR heldUntil < :now`) plus a `Put` of the booking. It succeeds or fails as a whole, which is Block 1's all-or-nothing rule without locks.
- **A5** uses DynamoDB TTL on a separate hold item, or a sparse GSI on `heldUntil`. Remember TTL deletion is not immediate, so the condition in A2 must still check `heldUntil` itself.

**Hold transaction (sketch, AWS SDK for .NET)**

```csharp
var items = seatKeys.Select(k => new TransactWriteItem {
    Update = new Update {
        TableName = Table,
        Key = k,
        UpdateExpression = "SET #s = :held, bookingId = :b, heldUntil = :until",
        ConditionExpression = "#s = :available OR heldUntil < :now",
        ExpressionAttributeNames = new() { ["#s"] = "status" },
        ExpressionAttributeValues = new() {
            [":held"] = new("held"), [":available"] = new("available"),
            [":b"] = new(bookingId), [":until"] = new() { N = until.ToString() },
            [":now"] = new() { N = now.ToString() }
        }
    }
}).Append(new TransactWriteItem { Put = bookingPut }).ToList();

// TransactionCanceledException => 409, with CancellationReasons naming the taken seats
await dynamo.TransactWriteItemsAsync(new() { TransactItems = items, ClientRequestToken = idempotencyKey });
```

`ClientRequestToken` makes the transaction idempotent for a short window, which pairs with Block 1's idempotency keys.

**Cosmos DB design (NoSQL API)**

Cosmos DB forces a different decision. A transactional batch only works inside one logical partition, so the partition key decides which holds can be atomic.

| Container | Partition key | Items | Serves |
| --- | --- | --- | --- |
| `inventory` | `/eventSection` (for example `E1#A`) | Seat documents (`id = seatId`, status, bookingId, heldUntil) + one section summary document | A1, A2, A3 |
| `bookings` | `/bookingId` | Booking documents | A6, plus A4 through a change-feed projection |
| `userBookings` | `/userId` | Projection maintained by an Azure Function on the change feed | A4 |

- **A2** is a `TransactionalBatch` of patch operations with a filter predicate (`FROM c WHERE c.status = 'available' OR c.heldUntil < @now`) on each seat, all in one `eventSection` partition. Any failed condition aborts the whole batch.
- **Consequence:** one hold can only include seats from one section. Either accept that as a product rule (most ticketing sites already do), or fall back to per-seat optimistic concurrency with ETags (`IfMatchEtag`) plus a compensating release, and write an ADR on which you chose.
- **A5** uses Cosmos DB TTL on a separate hold document, with the same caveat as DynamoDB: the condition in A2 must still check `heldUntil`.
- Use the **session** consistency level for user-facing reads (read-your-writes per client) and understand why the hold itself is safe regardless: the batch's conditions run against the partition's current state.

Write one `ISeatInventory` interface in the booking service with two adapters, `DynamoSeatInventory` and `CosmosSeatInventory`, and run the same concurrency test against both.

## Infrastructure as code and delivery

Nothing is created by clicking in the console. Everything is Terraform, so any environment can be destroyed and recreated in under 30 minutes, and that is tested.

**Terraform layout**

```
infra/
  aws/
    modules/        # network (VPC, 3 AZ subnets), ecs-service, data (Aurora, DynamoDB,
                    # ElastiCache), messaging (SNS/SQS + DLQs), observability (ADOT, alarms)
    envs/dev/  envs/prod/
    bootstrap/      # S3 state bucket with locking
  azure/
    modules/        # network (VNet, subnets, private endpoints), container-app,
                    # data (Postgres Flexible, Cosmos DB, Azure Cache for Redis),
                    # messaging (Service Bus + DLQs), observability (App Insights, alerts)
    envs/dev/  envs/prod/
    bootstrap/      # Storage account + container for state (blob lease locking)
  shared/
    contracts.md    # what every module must output: service URL, queue names,
                    # secret references, so the app config looks the same on both clouds
```

- Remote state per cloud and per environment: S3 with locking on AWS, an Azure Storage blob container (lease-based locking) on Azure.
- Providers: `hashicorp/aws` and `hashicorp/azurerm` (plus `azapi` for resources azurerm doesn't cover yet). Pin versions.
- Keep modules cloud-specific. Don't build a "universal" abstraction over both clouds: the shared part is the output contract, not the resources.
- Run `terraform fmt`, `validate`, `tflint` and `checkov` (or `trivy config`) in CI for both trees.
- No secrets in state or variables: reference Secrets Manager or Key Vault entries by ARN or ID, and use managed identities / IAM task roles instead of connection strings with passwords wherever the service supports it.

**CI/CD pipeline (GitHub Actions)**

1. On pull request: build and unit-test all services once, then `terraform plan` for dev on both clouds (a matrix job), and post both plans as PR comments.
2. On merge to `main`: build images once, scan them, push the same digest to ECR and ACR, `terraform apply` dev on each cloud, then run smoke tests and a short k6 run against both.
3. Manual approval per cloud, then deploy to prod: ECS rolling deployment with the deployment circuit breaker and automatic rollback on AWS; Container Apps revisions with traffic splitting on Azure.
4. Authenticate with OIDC federation on both: an IAM role trusting GitHub's OIDC provider on AWS, and an Entra ID app registration with a federated credential on Azure. No long-lived cloud keys in GitHub.

**Database migrations** run as a separate one-off task before the new version receives traffic, and every migration must be backward compatible with the previous app version (expand, then contract).

## Reliability and disaster recovery

Ticket Rush runs Multi-AZ in one region, plus a warm-standby plan for a second region that is written, costed and tested once, but not left running. That's the usual answer for a system whose worst failure is a missed sale, not lost money.

**Set targets first**

| Data / function | RPO (max data loss) | RTO (max downtime) | Why |
| --- | --- | --- | --- |
| Confirmed bookings and payments | 0 within a region; ≤ 1 min across regions | ≤ 15 min | Money and legal record |
| Seat holds | Can be lost (they expire in 10 min anyway) | ≤ 15 min | Users re-hold |
| Availability map cache | Rebuilt from the database | ≤ 5 min | Derived data |
| Notifications | ≤ 5 min | ≤ 1 h | Can be resent from bookings |

Challenge these numbers in your own ADR; the point is to have targets you can test.

**DR strategies compared** (cost rises as RTO falls)

| Strategy | What runs in region 2 | Typical RTO | Fits Ticket Rush? |
| --- | --- | --- | --- |
| Backup and restore | Nothing; backups copied cross-region | Hours | Too slow for a sale day |
| Pilot light | Data replicated (Aurora Global Database, DynamoDB global tables); no compute | Tens of minutes | Possible off-season |
| Warm standby | Data replicated + a scaled-down copy of the services | Minutes | **Chosen for sale days** |
| Multi-site active-active | Full capacity in both regions | Near zero | Hard: seat holds need one writer per seat, so conflicts across regions must be designed for |

**Active-active trap (both clouds):** DynamoDB global tables in their default eventually consistent mode, and Cosmos DB with multi-region writes under its default last-writer-wins conflict policy, both let two regions accept a write to the same seat. Both would "succeed", which is Block 1's overselling bug at continental scale. Either pin each event's seats to a home region and route holds there, or study the stronger options (DynamoDB multi-Region strong consistency; Cosmos DB single write region with strong or bounded-staleness reads) and their latency and region constraints. Write down which you'd pick on each cloud.

**Within one region**

- Services in at least 2 AZs (3 in prod), with health checks and autoscaling on CPU and request count.
- Aurora/RDS Multi-AZ; test failover with a forced reboot and measure the client error window.
- SQS and DynamoDB are regional and multi-AZ by design; ElastiCache with Multi-AZ automatic failover.
- Timeouts and retries from Block 2 tuned to the managed services' failover times.

**Same targets, different mechanisms**

| Concern | AWS | Azure |
| --- | --- | --- |
| Compute across zones | ECS service spread over 2–3 AZ subnets | Zone-redundant Container Apps environment, min replicas ≥ 2 |
| Relational HA | Aurora/RDS Multi-AZ | Postgres Flexible Server with zone-redundant HA |
| Seat store | DynamoDB (regional, multi-AZ by design) | Cosmos DB with availability zones enabled |
| Cross-region data | DynamoDB global tables, Aurora Global Database | Cosmos DB extra read region, Postgres geo-replica (read replica in another region) |
| Messaging | SQS/SNS (regional) | Service Bus Premium with zone redundancy; geo-replication or a paired namespace for DR |
| Fault injection | AWS Fault Injection Service | Azure Chaos Studio |

Run the same drill on both clouds and compare the measured error windows. Differences in failover time are a real input to choosing a cloud for a system like this.

## Cost model

Architects are judged on the bill as much as on uptime. Build the model in a spreadsheet from traffic assumptions, price it with the official calculators, then check it against Cost Explorer after your own load tests.

**Traffic assumptions** (adjust them, but write them down)

| Assumption | 1× | 10× | 100× |
| --- | --- | --- | --- |
| Events on sale per month | 20 | 200 | 2,000 |
| Peak concurrent users in a sale | 10,000 | 100,000 | 1,000,000 |
| Bookings per month | 50,000 | 500,000 | 5,000,000 |
| API requests per month | 50 M | 500 M | 5 B |
| Messages per booking | 6 | 6 | 6 |
| Log volume per month | 20 GB | 200 GB | 2 TB |

**Cost lines to estimate** (fill in from the calculators for your region; build this table once per cloud, using the mapped service on Azure: Container Apps for Fargate, Application Gateway for ALB, Postgres Flexible for Aurora, Cosmos DB RU/s for DynamoDB capacity, Service Bus for SNS/SQS, Azure Monitor for CloudWatch)

| Line item | Driver | 1× $/mo | 10× $/mo | 100× $/mo | Notes |
| --- | --- | --- | --- | --- | --- |
| ECS Fargate tasks | vCPU-hours, GB-hours |  |  |  | Baseline + sale-day scaling |
| ALB / API Gateway | LCUs or requests |  |  |  | API Gateway per-request pricing grows fast |
| Aurora / RDS | Instance hours, storage, I/O |  |  |  | Multi-AZ doubles instances |
| DynamoDB | On-demand read/write units, storage, GSIs |  |  |  | Compare with provisioned + autoscaling at 10× |
| ElastiCache | Node hours |  |  |  |  |
| SNS + SQS | Requests |  |  |  |  |
| NAT gateway / data transfer | GB processed, cross-AZ, internet egress |  |  |  | Often the surprise line |
| CloudWatch / observability | Log GB ingested, metrics, traces |  |  |  | Sample traces at 10×+ |
| DR standby region | Replicated data + minimal compute |  |  |  |  |
| **Total** |  |  |  |  |  |
| **Cost per booking** | Total ÷ bookings |  |  |  | Should fall as scale rises |

**Questions the model must answer**

1. Which line dominates at each scale, and what's the first optimisation you'd make there?
2. At what scale does provisioned capacity (or Savings Plans / reservations) beat on-demand?
3. What does one flash sale cost on its own, compared with a quiet month?
4. How much does the DR standby add, and is that worth it against the revenue of one lost sale day?
5. If Ticket Rush were a real company, which cloud would you pick, and what single fact would change your mind?

**Cloud comparison** (fill in after both cost models and both drills; it feeds question 5)

| Criterion | AWS | Azure | Notes |
| --- | --- | --- | --- |
| Total $/mo at 1× |  |  |  |
| Total $/mo at 10× |  |  |  |
| Total $/mo at 100× |  |  |  |
| Cost per booking at 10× |  |  |  |
| Seat-store pricing model | On-demand or provisioned capacity units | Provisioned, autoscale or serverless RU/s | Which one is cheaper for spiky flash sales? |
| Measured DB failover error window (s) |  |  | From the week 23/24 drills |
| Measured zone-loss error window (s) |  |  |  |
| Hours to deploy from scratch |  |  | First time and second time |
| Things that didn't translate |  |  | For example: one-section-per-hold rule on Cosmos DB |

## Week-by-week milestones

Block 4 now runs 8 weeks. Each week ends with a dated note in `/docs` and is ticked off only when every acceptance criterion holds. Weeks 21–24 spend real money, so plan those sessions and tear down afterwards.

| Week | Focus | Cloud |
| --- | --- | --- |
| 19 | Guardrails and Well-Architected review | Both |
| 20 | Cloud-native redesign on paper | Both |
| 21 | Deploy with Terraform | AWS |
| 22 | Port and deploy | Azure |
| 23 | Failure and DR drill | AWS |
| 24 | Failure and DR drill, then compare | Azure |
| 25 | Cost models and cloud comparison | Both |
| 26 | Certification and wrap-up | One exam now, one scheduled |

### Week 19 — Guardrails and a Well-Architected review

- [ ] Set up both accounts, budgets, tags and access from the guardrails checklist
- [ ] Read the pillar summaries of the AWS Well-Architected Framework and the Azure Well-Architected Framework; note where they group concerns differently
- [ ] Review the Block 2 system with the AWS Well-Architected Tool and the Azure Well-Architected Review assessment, answering honestly for a hypothetical production launch
- [ ] Merge the findings into one backlog: at least 2 high-risk issues per pillar, ranked

**Acceptance:** one review document with a risk list per pillar, noting where the two frameworks flagged different things, and the top 10 items to address in weeks 20–25.

### Week 20 — Cloud-native redesign on paper

- [ ] Finalise the service mapping for both clouds; write ADRs for the 3 hardest choices on each
- [ ] Design both seat stores from the access patterns: the DynamoDB single table and the Cosmos DB containers
- [ ] Write `ISeatInventory` with both adapters; run Block 1's 200-task concurrency test against DynamoDB Local and the Cosmos DB emulator
- [ ] Decide how to handle the Cosmos DB one-partition batch limit (section-per-hold rule or ETag fallback), as an ADR
- [ ] Threat model the public surface once (STRIDE, one page), then map each mitigation to both clouds

**Acceptance:** zero double-holds against both emulators, and both architecture diagrams and the ADRs reviewed by someone else.

### Week 21 — Deploy on AWS

- [ ] Bootstrap remote state; network module (3 AZs, private subnets for services and data)
- [ ] Data, messaging and ECS service modules; secrets from Secrets Manager; IAM task roles
- [ ] DynamoDB Streams → Lambda relay → SNS for the outbox
- [ ] GitHub Actions with OIDC: plan on PR, apply to dev on merge, manual gate for prod
- [ ] OpenTelemetry exporter switched to ADOT/CloudWatch; dashboards and 2 alarms recreated
- [ ] Small L1 load run against dev; destroy and recreate dev, timed

**Acceptance:** `terraform apply` from an empty account brings up a working Ticket Rush in under 30 minutes, and I1–I6 and M1–M7 pass after the load run.

### Week 22 — Port to Azure

- [ ] Bootstrap state in Azure Storage; network module (VNet, subnets, private endpoints, private DNS zones)
- [ ] Container Apps environment (zone-redundant), Postgres Flexible Server, Cosmos DB, Azure Cache for Redis, Service Bus, Key Vault; managed identities for every app
- [ ] Cosmos DB change feed → Azure Function → Service Bus for the outbox
- [ ] Add the Azure jobs to the same pipeline (OIDC with an Entra ID federated credential)
- [ ] Azure Monitor OpenTelemetry distro; recreate the same dashboards and alerts in Application Insights
- [ ] Same load run and the same destroy/recreate test
- [ ] Keep a "port log": every place the code, config or design had to change, and why

**Acceptance:** the same acceptance as week 21 on Azure, and a port log in which every change to business logic is justified (the goal is changes only in adapters and Terraform).

### Week 23 — Failure and DR on AWS

- [ ] AWS Fault Injection Service: stop tasks in one AZ, force an Aurora failover, add latency to a dependency; measure the error window with k6 running
- [ ] Stand up the warm-standby region from the same modules (DynamoDB global tables, Aurora Global Database)
- [ ] DR drill: declare region 1 down, fail over, measure actual RPO and RTO, fail back, tear down region 2

**Acceptance:** measured RPO/RTO for each scenario against targets, and a postmortem for every missed target.

### Week 24 — Failure and DR on Azure, then compare

- [ ] Azure Chaos Studio: the same three experiments (zone-down for Container Apps, Postgres HA failover, dependency latency)
- [ ] Warm-standby region: add a Cosmos DB read region and a Postgres geo-replica; run the same DR drill and tear down
- [ ] Fill in the failover rows of the cloud comparison table

**Acceptance:** the same measurements as week 23 on Azure, side by side, with an explanation for every difference over a few seconds.

### Week 25 — Cost and the cloud comparison

- [ ] Fill in the cost model at 1×, 10× and 100× for each cloud
- [ ] Compare each 1× estimate with real spend from weeks 21–24 (Cost Explorer, Cost Management), normalised for hours run
- [ ] Apply 3 optimisations per cloud and re-estimate (for example VPC endpoints or private endpoints instead of NAT, ARM-based compute, trace sampling, seat-store capacity mode, log retention)
- [ ] Complete the cloud comparison table and answer question 5 in a one-page memo

**Acceptance:** both cost models explain their 1× gap to real spend, and the memo picks a cloud with numbers.

### Week 26 — Certification and wrap-up

- [ ] Take one architect exam now: AWS Certified Solutions Architect – Associate, or Azure AZ-305 (the Azure Solutions Architect Expert certification also requires AZ-104). Check the current exam guides first, since versions change.
- [ ] Book the other cloud's exam within the next 3 months; your week 22 port log is a good study guide for it
- [ ] Two full practice exams before the first; review every wrong answer against the docs
- [ ] Write the capstone summary and the 6-month retrospective

**Acceptance:** one exam passed or booked, the second one scheduled, and the capstone summary published.

## Deliverables and capstone portfolio

Block 4's deliverables close the whole 6-month plan. Put them together so a hiring manager or architecture reviewer can follow the story in 10 minutes.

**Block 4 deliverables**

- [ ] Well-Architected review (both frameworks) with the risk backlog and what you fixed
- [ ] Both architecture diagrams and the ADRs for each cloud
- [ ] Terraform for AWS and Azure plus the shared pipeline (public repo, no secrets, no account or subscription IDs)
- [ ] The port log from week 22
- [ ] Failure and DR report: measured RPO/RTO on both clouds against targets, with graphs
- [ ] Cost models for both clouds at 1×, 10× and 100×, the completed cloud comparison table, and the one-page "which cloud" memo
- [ ] One certification passed, the second booked

**Capstone repository README outline**

```markdown
# Ticket Rush — designing a flash-sale booking system, from a monolith to two clouds

## The problem
10,000 users, 500 seats, 60 seconds. No overselling, no double charges.

## Evolution
| Version | Architecture | Key lesson | Link |
| --- | --- | --- | --- |
| 1 | Monolith + Postgres | Concurrency control choices, with benchmarks | /docs/block1 |
| 2 | Event-driven services + saga | Outbox, idempotency, resilience, postmortems | /docs/block2 |
| 3 | Raft + KV store (write-up only) | Consensus and linearizability from first principles | /docs/block3 |
| 4 | Cloud-native on AWS and Azure | Managed-service trade-offs, porting, DR, cost, cloud choice | /docs/block4 |

## Results in numbers
Throughput, p99, failover window, RPO/RTO measured, cost per booking.

## Decisions I'd defend (and the ones I'd change)
Links to ADRs.

## What I'd do at 100x
```

**Interview kit:** turn each block into a 3-minute story (problem, decision, numbers, what you'd do differently). These answer the "tell me about a system you designed" question with evidence instead of slogans.

## Stretch goals and pitfalls

**Stretch goals** (only after week 26 is done)

- **Kubernetes:** move the services to EKS and AKS with the same Helm charts; compare operational effort with ECS and Container Apps honestly.
- **Event-driven autoscaling:** scale payment consumers on queue depth (ECS target tracking on SQS metrics; Container Apps KEDA scale rules on Service Bus).
- **Edge waiting room:** a virtual queue at the edge (CloudFront + Lambda@Edge, or Front Door + rules) that admits users at a fixed rate before they reach the API.
- **Cross-cloud DR (paper only):** design how Ticket Rush would fail over from AWS to Azure, and list everything that makes it hard (data replication, DNS, identity, secrets). Usually the conclusion is that multi-region in one cloud is the better buy.
- **Professional-level certifications:** AWS Solutions Architect – Professional and the second Azure exam path, 3–6 months later.

**Pitfalls to avoid**

- **Surprise bills:** NAT gateways, idle load balancers, forgotten second regions and verbose logs cost money while you sleep. Budgets plus same-day teardown.
- **ClickOps:** anything created by hand drifts from Terraform and breaks the recreate test. Import it or delete it.
- **Lift-and-shift thinking:** putting Postgres row locks into DynamoDB doesn't work. Redesign around conditional writes and access patterns.
- **Managed-service limits:** check quotas (transaction item limits, message sizes, API rate limits, Fargate task limits) before load testing, not during.
- **Treating Multi-AZ as DR:** Multi-AZ protects against a data centre failure, not a region outage, a bad deploy or a deleted table. Backups and point-in-time recovery are separate.
- **Leaking credentials:** never commit `.tfstate`, `.env` files or account IDs; use OIDC for CI and scan the repo with a secrets scanner before making it public.
- **Certification as the goal:** the exam checks breadth of vocabulary. The decisions, numbers and postmortems in your repo are what demonstrate judgment.
- **Lowest common denominator:** forcing both clouds into one generic abstraction throws away what each does well. Share interfaces in the app and output contracts in Terraform, not resources.
- **Shallow on both:** doing each cloud at half depth teaches less than doing one properly. If time runs short, finish AWS end to end, then do a smaller Azure port (booking service + Cosmos DB + Service Bus) rather than two half-finished deployments.
