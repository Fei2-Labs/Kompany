# ADR-0005: Concurrent, resilient autonomous runtime (lanes on top of the daemon)

**Status:** Proposed (2026-06-13) — drafted from live Swedexpress operating incidents; awaiting founder acceptance
**Deciders:** Founder (solo)

## Context

Kompany **already has** a self-driving daemon: `kompany daemon install` registers a launchd LaunchAgent that ticks every ~5 minutes — heartbeat, then **at most one task advanced per tick** under the same budget caps and approval gates — and the desktop app attaches to it. The supporting primitives exist too (`runtime_status`, `heartbeat`, `suspend`/`resume`, `episodes`/`memories`, `approvals`). So the *always-on, session-independent* foundation is real.

What is missing — and what this ADR addresses — is everything the daemon needs to run work **correctly and concurrently** instead of one-task-per-tick-and-hope:

1. **Concurrency.** The daemon advances at most one task per tick — a single sequential lane. There is no model for several independent workstreams running in parallel without interfering.
2. **Model resilience.** Nothing in the tick loop survives a single-model outage.
3. **Honest failure semantics.** A tick that cannot work because the model is down must fail loudly, not report success.

The *parallel* and *ad-hoc* autonomy actually run day-to-day today lives **outside** the daemon — a chat `/loop` plus a bolted-on launchd worker — and that is where both incidents below originated (2026-06-13):

1. **Silent success on model outage.** The primary model was unavailable ~7h. The external worker fired on schedule, but the CLI defaulted to one model, got "currently unavailable", and **exited 0 having done nothing** — three empty cycles, no alarm. Every in-session wakeup died too because the host session shared the unavailable model. The system reported healthy while doing nothing.
2. **Single-thread death.** A chat turn ended on a malformed tool call *without scheduling the next wakeup*; the session loop silently stopped for ~5h. The independent launchd worker kept running fine throughout — which is the whole point.

Requirement this settles: **once work is decoupled from any chat client, Kompany must continue independently and correctly, with multiple concurrent threads that do not interfere.**

A framing question came up — record/handle continuity as a *dev-inbox* (capture + triage incoming work) or a *handoff* (resume-where-I-left-off state)? They are **not alternatives**; they are two complementary layers a correct autonomous runtime needs at once.

## Decision

Extend the existing daemon from a single sequential ticker into a **dispatcher over N independent lane-workers**. Continuity rests on two distinct durable layers:

### 1. Intake queue — the *dev-inbox* pattern ("what work is waiting")
An append-only queue of incoming work (directives, tasks, signals), triaged and routed to a lane, surviving restarts. Answers *what to do next*. Producer/consumer: anything enqueues; a worker dequeues; nothing is silently lost.

### 2. Continuity state — the *handoff-to-self* pattern ("who am I / where was I / what's in flight")
A durable layer **every amnesiac tick reads first** to resume correctly: identity + standing directives, the in-flight work ledger (started-but-unfinished), and episodic memory. The engine-native version of the journal/standing-directives files a headless worker reads on wake. Answers *how to continue correctly* across a restart, model swap, or crash.

> dev-inbox = pending work. handoff = identity + in-flight state. They sit side by side. Recording *this decision itself* uses neither — it is an ADR, a standing architectural choice, not a transient capture or a one-shot resume note.

### Lane-worker contract (concurrency without interference)
Each lane-worker:
- has its **own dispatch cadence**;
- holds its **own lease/lock** (never re-enters / double-runs);
- owns its **own state partition** — **two lanes never write the same record/file**;
- runs a **model-fallback pool** (primary → fallbacks) so one model outage cannot stall it;
- coordinates with other lanes **only** through three shared surfaces, never by editing another lane's state:
  1. **git** (for file-publishing lanes) — and **`pull --rebase` before every push** (a push race otherwise rejects the push and silently stops downstream publishing);
  2. **the engine** — directives / approvals / episodes as the shared brain;
  3. **the intake queue** — hand work to another lane by enqueueing, not by reaching in.

The genuinely shared resources (git/publish, the ledger) are serialized by lease + rebase. Everything else is partitioned, so lanes run truly concurrently with zero write contention.

### Hard failure-mode rules (from the incidents)
- **"Model unavailable" is a tick/cycle FAILURE, not success** — non-zero result + a health record + the normal alert path. Silent success is the worst failure mode of an autonomous system; treat it as a bug.
- **No critical autonomy may live only in a chat session.** The chat client is supervisor/observer; the daemon and lane-workers are the doers. A dead or model-down chat session must change nothing.

## Consequences

- The daemon gains a lane registry + dispatcher; the chat client becomes optional tooling, not a dependency.
- Net-new design surface: the in-flight work ledger schema, lane partitioning/leasing, model-fallback config, and the silent-success → failure path.
- Open questions: how lanes claim/lease queue items without a central broker; backpressure when a lane falls behind; whether `suspend` halts dispatch only or the whole daemon; per-lane vs global budget caps.

## Validation source

Swedexpress is the live test harness. These mechanics were proven the hard way in production ops on 2026-06-13 (the outage and the loop gap above); incident reports from that harness feed Kompany's runtime backlog directly.
