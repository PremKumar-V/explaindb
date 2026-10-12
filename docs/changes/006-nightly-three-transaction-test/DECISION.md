# 006 — Nightly three-transaction serializability test
**Date:** 2026-10
**MR:** https://gitlab.cs.uni-saarland.de/bigdata/dbsys/explaindb/-/merge_requests/139
---
## Context

Feature 005 checks every schedule of two transactions against a serial replay. Some anomalies need three
transactions, e.g. the read-only anomaly of Fekete et al. (a read-only transaction makes an otherwise serializable
pair non-serializable), and validation method 2 has a code path that only matters when two transactions commit
while a third one is still running. 005 already decided to run three transactions in a nightly pipeline only: the
number of schedules grows quickly with the number of transactions, and the regular test job already takes about
four minutes on CI.

## Decision

- The existing test is parameterised by a schedule space: initial state, operations, and the allowed numbers of
  data operations per transaction (a set closed under reordering). The same code runs any number of transactions;
  two spaces are configured: two transactions (unchanged, 255,960 schedules per validation method, in the regular
  test job) and three transactions (nightly).
- Transactions are numbered in the order of their begin, as the store does, so every schedule runs once.
- Three transactions: at most one transaction with two data operations, the others with one, over six operations
  (read with a == 1, read all, update x out of the predicate, delete x, insert y into the predicate, move z into
  the predicate): 2,782,080 schedules per validation method. This includes the Fekete pattern and one
  representative per case of the validation.
- The nightly test is skipped unless `EXPLAINDB_NIGHTLY=1`. It also asserts that some schedules let two writers
  commit while a third writer is running.
- A CI job runs it without coverage in scheduled pipelines; in merge requests it can be started by hand and does
  not block. Scheduled pipelines run only this job and the dependency audit (which reports new advisories without
  a commit), not the notebook tests or the documentation deployment.
- The oracle gets a self-test with three transactions in which only the third one's read is inconsistent.

## Consequences
**Positive:** Anomalies that need three transactions are checked every night on the real code, with the same
oracle and failure table as the two-transaction test. Adding a run with more transactions is one more schedule
space.
**Negative:** A violation found by the nightly run is only reported the next morning, not in the merge request
that introduced it (unless the job is started by hand). The six operations leave out three variants (content
change staying selected, unchanged content, insert outside the predicate) that the two-transaction run covers.
The nightly run takes an estimated 30–40 minutes on CI.

## Out of scope & deferred
- The indexed store (blocked by #54).
- Building the failure table text only on failure (speed-up, only if the runtime requires it).
- Four or more transactions.
- A regular-CI regression test for the fixed Fekete schedule on the real store (with this store it does not
  distinguish the three-transaction case from the two-transaction one).

## Alternatives rejected
- Three transactions in the regular CI job: too slow.
- All nine operations for three transactions: about 14 million schedules per validation method, hours on CI.
- Three operations only (enough for the Fekete pattern): misses predicate reads and deletes, where earlier bugs
  were.
- Skipping interleavings whose operations should commute: assumes the behaviour under test (as in 005).

## Scope-shift 2026-10-09

The nightly run gets a second schedule space with three transactions, two of which read and write: data operation
counts (2, 2, 1) and their reorderings, over four operations (read with a == 1, update x out of the predicate, insert y
into it, move z into it), 5,913,600 schedules per validation method. The first three-transaction space has at most
one transaction that both reads and writes, so a write skew between two of three transactions was not covered.
Read all and delete stay in the first space only; six operations would give about 45 million schedules. The
nightly run then takes an estimated 60–70 minutes on CI; the job timeout is raised to 3 hours.

## Scope-shift 2026-10-09 (2)

As the nightly run took only about an hour on CI, it gets three more checks:
- Voluntary aborts: every transaction of the two-transaction space ends with commit or with its own abort
  (1,023,840 schedules per validation method). So far, only the store aborted transactions.
- Longer transactions: two transactions with one to three data operations each over the six data operations of the
  first three-transaction space (6,984,288 schedules per validation method), e.g. read, write and read again.
- The regular two-transaction test under three further hash seeds, as bug #63 depended on the hash seed.

The nightly run then takes an estimated two hours on CI; the job timeout is raised to 4 hours. Three transactions
that all read and write (about 24 million schedules per method) stay out: about three more hours.

<!-- notes below: skill never overwrites past this marker -->

**Note 2026-10-09 (measured):** the nightly tests (both three-transaction spaces, both validation methods, about
17.4 million schedules) take 963 s locally, so about an hour on CI (about four times slower). The regular test
file takes about 23 s locally.

**Note 2026-10-10:** two longer transactions with three data operations each made the nightly run take about an
hour locally (about four hours on CI). The longer-transaction space now allows three data operations for at most
one of the two transactions: 1,105,632 instead of 6,984,288 schedules per validation method.

**Note 2026-10-11 (module location):** the schedule model, enumeration, runner and oracle moved from
`system/tests/serializability_oracle.py` to `system/playground/schedules.py`, so that a notebook for students can use
them (feature 009) without importing from the tests. The test configuration (initial state, data operations, schedule
spaces) stays in `system/tests/test_mvcc_serializability.py`. The module is now part of the API documentation.

**Note 2026-10-12 (current state):** the Decision and Consequences sections above describe the first version. Five
schedule spaces are configured now: `TWO_TRANSACTIONS` in the regular test job; `THREE_TRANSACTIONS`,
`THREE_TRANSACTIONS_TWO_READ_WRITE`, `TWO_TRANSACTIONS_WITH_ABORT` and `TWO_LONGER_TRANSACTIONS` nightly (see the
scope shifts). The nightly run takes an estimated two hours on CI, with a timeout of 4 hours. The six operations of
`THREE_TRANSACTIONS` contain one representative of each case of the validation except the three left-out variants
listed under Consequences; only the nine operations of the two-transaction run contain all cases.
`system/tests/test_ci_config.py` checks that only the nightly job and the dependency audit run in scheduled
pipelines.

The Fekete pattern is the read-only anomaly described by A. Fekete, E. O'Neil and P. O'Neil, "A Read-Only
Transaction Anomaly Under Snapshot Isolation", SIGMOD Record 33(3), 2004.
