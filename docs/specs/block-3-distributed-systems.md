# Ticket Rush — Block 3 Spec

_Spec as of 2026-11-05. Source: https://claude.ai/artifact/Nm8D3pTN488YCJz1cdgR2p_

## Overview

Block 3 is six weeks of building consensus yourself. You implement Raft and a linearizable, fault-tolerant key-value store on top of it, using the MIT 6.5840 labs and their test harness. Then you turn what you learned back onto Ticket Rush.

**By the end of Block 3 you can:**

- Explain why distributed systems are hard: partial failure, unreliable networks, unreliable clocks (DDIA ch. 8).
- Define linearizability, serializability and eventual consistency precisely, and say which one a given design provides.
- Implement Raft (leader election, log replication, persistence, snapshots) that passes the lab tests hundreds of times in a row.
- Build a replicated state machine with exactly-once client semantics, the same idea as Block 2's inbox, but inside consensus.
- Compare leader-based consensus with leaderless quorums (Dynamo) and the actor model (Orleans), and pick one for a given problem.

**Connection to Ticket Rush:** Blocks 1 and 2 relied on Postgres and RabbitMQ to be correct. Block 3 opens that box. In week 18 you write a design for a seat inventory that survives a datacenter losing a machine without a single-node database.

**Out of scope:** Byzantine fault tolerance, blockchains, and production-grade storage engines.

## Language and setup

Use Go for the labs. The lab skeleton, the simulated network that drops, delays and reorders messages, and the tests are all in Go. That test harness is most of the value. Coming from C#, you can be productive in Go in about a week.

| Option | Pros | Cons | Verdict |
| --- | --- | --- | --- |
| Go with the official labs | Battle-tested tests, linearizability checker included, huge community of write-ups | New language | **Recommended** |
| C# port | Your main language; `Channel<T>` and `async` map well onto Raft | You must build the simulated network and tests yourself, and weak tests hide bugs | Only after Go, as a second implementation |

**Setup**

- Go (the version the current course pages ask for), VS Code with the Go extension or GoLand
- Clone the lab skeleton from the [MIT 6.5840 course site](https://pdos.csail.mit.edu/6.824/). Lab numbering and part names change between years, so follow the current schedule. This spec refers to the labs by topic: MapReduce, Raft, fault-tolerant KV.
- Learn just enough Go first: goroutines, channels, `sync.Mutex`, `sync.Cond`, `select` with `time.After`, and the race detector (`go test -race`).

**Lab code rules.** MIT asks that lab solutions are not published. Keep your lab repository private, and share your write-ups, diagrams and test statistics publicly instead.

## Reading plan

Read each item before the week's coding, not after. The papers explain exactly the bugs you are about to write.

| Week | Read | Focus on |
| --- | --- | --- |
| 13 | DDIA ch. 8 “The Trouble with Distributed Systems”; MapReduce paper (Dean & Ghemawat, 2004) | Partial failure, timeouts, process pauses, clocks, fencing tokens |
| 14 | Raft extended paper (Ongaro & Ousterhout, 2014), sections 1–5; the course's Raft guidance pages | Figure 2 is the specification: every sentence in it matters |
| 15 | DDIA ch. 9 “Consistency and Consensus”; Raft paper sections 5.4–5.6 and 8 | Linearizability, ordering, the commit rule, client interaction |
| 16 | Raft paper section 7 (log compaction); Lamport, “Time, Clocks, and the Ordering of Events” (1978) | Snapshots, happens-before, logical clocks |
| 17 | DDIA ch. 10–11 skim; Herlihy & Wing on linearizability (optional); a Jepsen analysis of your choice from jepsen.io | How linearizability is tested in real systems |
| 18 | Dynamo paper (DeCandia et al., 2007); Orleans “Virtual Actors” paper (Bernstein et al.); DDIA ch. 12 | Quorums, sloppy quorums, vector clocks, actor placement |

**Habit:** after each paper, write five lines in `/docs/reading.md`: the problem, the key idea, one trade-off, one thing that surprised you, and where Ticket Rush would hit the same issue.

## Raft roles

Every Raft server is in one of three roles, and terms act as a logical clock that settles any conflict between them. Week 14 is about getting these transitions exactly right.

> Diagram: Raft roles · 3 states, 6 transitions — see the source doc linked above.

The rule that prevents split brain: every RPC request and reply carries a term, and any server that sees a higher term updates its own and becomes a follower immediately.

## Safety properties and what the tests check

Raft guarantees five properties (paper, Figure 3). These are Block 3's invariants, just as I1–I6 and M1–M7 were for the earlier blocks. Add your own assertions for them in debug builds instead of waiting for a test to fail far from the bug.

| Property | Meaning | Cheap assertion you can add |
| --- | --- | --- |
| Election safety | At most one leader per term | Log every `becomeLeader(term)`; a checker script fails if a term appears twice |
| Leader append-only | A leader never overwrites or deletes its own entries | Panic if a leader's log shrinks |
| Log matching | Same index and term ⇒ identical logs up to that index | Test harness checks this through applied commands |
| Leader completeness | A committed entry is in every later leader's log | Panic if a new leader lacks an entry it has seen committed |
| State machine safety | No two servers apply different commands at the same index | The harness compares every `ApplyMsg` across servers |

**Liveness** is not guaranteed by Raft, but the tests demand it: a leader must be elected within about 5 seconds of a stable majority existing, and agreement must resume after a partition heals.

**KV layer (week 17)** adds two more:

- **Linearizability:** the history of client `Get`/`Put`/`Append` calls must be explainable by a single sequential order consistent with real time. The labs check this with the Porcupine checker.
- **Exactly-once execution:** a retried client request (same client ID and sequence number) is applied once, even across leader changes. This is Block 2's inbox table, now living inside the replicated state machine.

## The system you build

By week 17 you have a replicated state machine: three KV servers, each sitting on its own Raft peer, which together survive any single crash or partition.

> Diagram: fault-tolerant KV on Raft · 3 replicas — see the source doc linked above.

Each KV server hands operations to Raft with `Start()` and applies them only when they come back committed on `applyCh` (labelled on server 1; the other two are the same). Only the leader accepts `Start()`; the others reply `ErrWrongLeader` and the clerk tries the next server.

| Layer | You write | The lab gives you |
| --- | --- | --- |
| Clerk | Leader discovery, retries, clientId + seq | RPC client stubs |
| KV server | Apply loop, duplicate table, waiting for commit, snapshots of the KV map | Server skeleton |
| Raft | Everything in Figure 2, plus snapshots | `Persister`, the `ApplyMsg` type |
| Network | — | `labrpc`: simulated, unreliable RPC with partitions |

## Testing and debugging method

A Raft test that passes once proves little. Bugs show up in 1 run out of 200, so the method is to run tests many times, log everything with structure, and read the logs as a timeline.

**1. Structured, filterable logs**

Every line carries elapsed time, server, term, role and a topic, so you can filter one topic and lay servers out in columns:

```go
// 001234 S2 T5 LEAD [APPEND] -> S0 prevIdx=41 prevTerm=4 entries=3
func (rf *Raft) dlog(topic string, format string, a ...interface{}) {
    if !debug { return }
    t := time.Since(start).Microseconds() / 100
    prefix := fmt.Sprintf("%06d S%d T%d %s [%s] ", t, rf.me, rf.currentTerm, rf.role, topic)
    log.Printf(prefix+format, a...)
}
```

Topics to use: `TIMER`, `VOTE`, `LEAD`, `APPEND`, `COMMIT`, `APPLY`, `PERSIST`, `SNAP`, `DROP`. Write a small script (Python or a .NET console app) that turns a log into one column per server, coloured by topic.

**2. Run tests many times, in parallel**

```bash
# run each test 500 times, 8 at a time, keep logs only for failures
for i in $(seq 1 500); do
  echo $i
done | xargs -P 8 -I{} sh -c 'go test -run 3B -race > logs/run-{}.log 2>&1 && rm logs/run-{}.log'
ls logs | wc -l   # number of failed runs
```

Always run with `-race` during development. A data race in Raft is a correctness bug, not a style issue.

**3. Debug from the first wrong line, not the panic**

Find the first line where some server did something Figure 2 doesn't allow, such as granting a vote it shouldn't or committing from an old term. The panic is usually much later.

**4. Common bug checklist** (go through it before reading 10,000 log lines)

- Resetting the election timer when you shouldn't (only on granting a vote, on valid AppendEntries from the current leader, or on starting an election)
- Not stepping down when you see a higher term in any RPC request or reply
- Using a reply from an old term (check that `currentTerm` hasn't changed after the RPC returns)
- Holding the lock while sending an RPC or writing to `applyCh` (deadlock)
- Committing an entry from a previous term by counting replicas (Figure 8)
- Forgetting to persist before replying to an RPC

## Week-by-week milestones

This is the hardest block. Budget 10–12 hours a week, and let weeks 15–16 spill over if needed. A Raft that passes 500 runs in a row is worth more than finishing on time.

### Week 13 — Go, failure models and MapReduce

- [ ] Learn Go basics: Tour of Go, then goroutines, channels, mutexes, `select` with timeouts
- [ ] Do the MapReduce lab: a coordinator hands out map and reduce tasks to workers over RPC, and reassigns tasks from workers that crash or stall (10 s timeout)
- [ ] Write intermediate files atomically (temp file + rename) so a crashed worker can't leave a half-written output
- [ ] Exercise: write a one-page note on fencing tokens (DDIA ch. 8) and where Block 1's Redis seat reservation needed one

**Acceptance:** all MapReduce tests pass 50 times in a row with `-race`.

### Week 14 — Raft leader election

- [ ] Implement the `Raft` struct with the exact fields from Figure 2 (no extras yet)
- [ ] Randomized election timeouts (for example 300–600 ms) and heartbeats (about 100 ms, within the tester's limit of 10 per second)
- [ ] `RequestVote` RPC with the up-to-date log check (even though logs are empty this week)
- [ ] One long-running ticker goroutine per server; no `time.Sleep` loops holding the lock
- [ ] Logging helper and the parallel test script from the method section

**Acceptance:** the election tests pass 500 times in a row with `-race`; your log viewer shows a clean election after a leader is disconnected.

### Week 15 — Log replication and persistence

- [ ] `Start()`, `AppendEntries` with consistency check, `nextIndex`/`matchIndex` per follower
- [ ] Commit rule: advance `commitIndex` only for entries from the current term (Figure 8)
- [ ] One applier goroutine that sends committed entries to `applyCh` in order, without holding the lock
- [ ] Fast log backtracking (conflict term and index in the reply) so the unreliable tests finish in time
- [ ] Persist `currentTerm`, `votedFor` and `log[]` through the `Persister` before responding to any RPC that changed them

**Acceptance:** the replication and persistence tests (including the unreliable "Figure 8" tests) pass 500 times in a row. Record total run time and RPC counts and stay within the course's limits.

### Week 16 — Snapshots and log compaction

- [ ] `Snapshot(index, data)` called by the service; trim the log and keep the last included index and term
- [ ] Re-index the log so every access goes through one helper that handles the offset (this is where most snapshot bugs live)
- [ ] `InstallSnapshot` RPC for followers that are too far behind; deliver the snapshot on `applyCh`
- [ ] Restore from snapshot plus persisted state after a crash

**Acceptance:** all Raft tests pass 500 times in a row with `-race`. Write a one-page note on the hardest bug you found: the log line that revealed it, the root cause and the fix.

### Week 17 — Fault-tolerant key-value store

- [ ] Clerk: send each request to the server it thinks is leader; retry another on `ErrWrongLeader` or timeout
- [ ] Server: `Start()` the operation, wait on a per-index channel until it's applied, and check that the applied entry is the one you submitted (a new leader may have overwritten it)
- [ ] Exactly-once: clerks attach (clientId, seq); the state machine keeps the last seq applied per client and ignores duplicates
- [ ] Snapshot the KV map and the duplicate table when the Raft state grows past `maxraftstate`
- [ ] Read Porcupine's output on a failing run and find the non-linearizable operation

**Acceptance:** all KV tests pass 200 times in a row, including the partition and snapshot tests. Explain in your notes why a `Get` must go through the log, or use a read-index or lease scheme, to stay linearizable.

### Week 18 — Other models, and back to Ticket Rush

- [ ] **Dynamo-style quorums:** in Go or C#, build a tiny leaderless store with N=3, configurable R and W, version vectors, and read repair. Show a stale read with R+W ≤ N and none with R+W > N.
- [ ] **Orleans:** build a `SeatGrain` per seat (or an `EventInventoryGrain` per event) in .NET. Run the Block 1 flash-sale scenario and compare contention with the Postgres strategies.
- [ ] **Design doc:** "Ticket Rush seat inventory without a single primary database." Compare three options: Raft-replicated store partitioned by event (like etcd or CockroachDB ranges), Orleans grains backed by storage, and Dynamo-style quorums. Say which one gives no overselling and why the quorum option doesn't by itself.

**Acceptance:** the quorum demo shows the stale read, the Orleans run passes I1–I5, and the design doc reaches a clear recommendation backed by the consistency guarantees each option provides.

## Deliverables and write-up template

The lab code stays private, so Block 3's public output is what you learned. That's also what interviewers want to hear about.

- [ ] **Test-run statistics:** a table of each test group, runs, failures, and average time
- [ ] **Bug journal:** the 3 hardest bugs, each with the log excerpt, root cause and fix
- [ ] **Diagrams:** your own Raft state diagram and KV architecture (redrawn, not copied)
- [ ] **Quorum demo and Orleans experiment:** code (these are your own projects, so they can be public) and results
- [ ] **Design doc:** Ticket Rush seat inventory without a single primary
- [ ] **Public blog post:** "What implementing Raft taught me about my production systems"

**Test-run statistics** (fill in as you go)

| Test group | Runs | Failures | Avg time (s) | Notes |
| --- | --- | --- | --- | --- |
| MapReduce | 50 |  |  |  |
| Raft election | 500 |  |  |  |
| Raft replication | 500 |  |  |  |
| Raft persistence | 500 |  |  |  |
| Raft snapshots | 500 |  |  |  |
| KV (no snapshots) | 200 |  |  |  |
| KV (snapshots) | 200 |  |  |  |

**Bug journal entry template**

```markdown
## Bug: <one-line symptom>

**Test / frequency:** TestFigure8Unreliable, ~1 in 150 runs
**First wrong log line:**
    012345 S1 T7 LEAD [COMMIT] commitIndex 38 -> 41
**Root cause:** (which Figure 2 rule was broken, and why the code broke it)
**Fix:** (the change, and why it is correct)
**Lesson for production systems:** (where Ticket Rush or work code could do the same)
```

**Design doc outline**

1. Requirements: no overselling, survive 1 machine failure, hold p99 < 300 ms
2. Options: Raft-partitioned store, Orleans grains, Dynamo quorums
3. For each: consistency guarantee, failure behaviour, latency cost, operational cost
4. Recommendation and the condition under which you'd change it
5. What you'd buy instead of build (for example etcd, CockroachDB, Spanner, Cosmos DB with strong consistency), and why

## Stretch goals and pitfalls

**Stretch goals** (only after week 18 is done)

- **Sharded KV:** the course's final lab. A config service (itself on Raft) assigns shards to replica groups, and shards move between groups while serving traffic.
- **Raft in C#:** port your Go Raft to .NET with `Channel<T>` and a simulated network you write yourself. Writing the test harness teaches as much as the algorithm.
- **Read optimizations:** implement ReadIndex or leader leases and measure how much `Get` latency drops.
- **Run a real system:** start a 3-node etcd cluster, kill the leader during a write loop, and watch the election in its logs. Do the same with CockroachDB and a Ticket Rush seat table.
- **TLA+:** read the Raft TLA+ spec, or model Ticket Rush's hold/confirm protocol in PlusCal and let the model checker find a race.

**Pitfalls to avoid**

- **Improvising on Figure 2:** "simplifying" a rule almost always breaks safety in a rare case. Implement it literally first.
- **Trusting one green run:** a single pass means little. Statistics over hundreds of runs are the only evidence.
- **Locking around I/O:** never hold the Raft mutex while sending an RPC, sleeping, or writing to `applyCh`.
- **Timer and term confusion:** most election bugs come from resetting the timer at the wrong moment or acting on a reply from an older term.
- **Copying solutions:** reading someone else's Raft before yours works removes most of the learning. Use the course guidance pages and the paper instead.
- **Treating this as academic:** every bug you find has a production equivalent (stale leaders, split brain, retries applied twice). Write that connection down each time; it's the real payoff of Block 3.
