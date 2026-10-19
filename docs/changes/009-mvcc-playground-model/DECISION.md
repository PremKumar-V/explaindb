# 009 — MVCC playground: model of schedules for students
**Date:** 2026-10
**MR:** https://gitlab.cs.uni-saarland.de/bigdata/dbsys/explaindb/-/merge_requests/142
---
## Context

Students should be able to play with schedules on the MVCC store (`TransactionalKeyValueStore`): build a schedule
by clicking, run it operation by operation forwards and backwards, load classic scenarios, compare the validation
methods, and see after each operation the versions in the store, what each transaction sees, read and wrote, the
schedule table and whether the outcome is serializable. The user chose a Jupyter notebook with ipywidgets (runs on
Binder). The schedule model, runner and serial-replay oracle exist (features 005/006, now
`system/playground/schedules.py`). This feature builds the model behind the notebook; the widgets follow in
feature 010.

## Decision

- A small pure Python model in `system/playground/` (no widgets), on top of `schedules.py`:
  - Three modes: validation method 1, method 2, and no validation (snapshot isolation only), so that anomalies can
    actually happen. The store without validation becomes production code; the existing test that uses it stays.
  - Running to an operation re-runs the schedule from its start on a fresh store with the existing runner
    (`run_schedule`, which gets an optional number of operations to run): the store cannot return to an earlier
    state, and runs are deterministic.
  - After any operation: the versions per object (commit timestamp, delete markers, the work-in-progress version and
    its writer), and per transaction its status, what it sees, its reads and the objects it wrote (also after an
    abort), with the names T1..Tn and the store's transaction ids.
  - Verdict: "pending" until every transaction has ended; then a replay of the committed transactions in the order
    of their serialization points, comparing read results and the final state; if that order does not match, all
    orders of the committed transactions are tried, distinguishing "serializable in another order" from "not
    serializable". An error of the store other than an abort (a delete of an object the store does not have) stops
    the run and becomes the verdict.
  - The schedule is not checked: the notebook's dropdowns only offer valid operations and limit the number of
    transactions (the verdict tries n! orders).
  - The model returns typed results (versions, transactions, verdict); the notebook (010) shows them.
  - Seven scenarios, written in Python: serial, lost update, write skew, phantom insert, concurrent delete,
    read-only transaction, and Fekete's read-only anomaly (three transactions); the outcome per mode is pinned in
    tests.

## Consequences
**Positive:** Students can build and replay schedules on the real store, see anomalies happen without validation and
see how each method prevents them. The store's teaching code stays as it is; the model reuses the runner and the
oracle of the serializability test.
**Negative:** The model does not explain why a transaction commits or aborts; students compare the modes and read the
store's validation code. A schedule built in code is not checked. Re-running from the start for every operation
costs time quadratic in the schedule length, which is negligible for teaching-sized schedules. Methods 1 and 2
decided alike on every schedule tested so far, so comparing them shows how they decide, not different outcomes.

## Out of scope & deferred
- The ipywidgets view, the notebook, the README row and the tutorial page (feature 010).
- Explanations of each commit and abort; a text format for schedules; both methods side by side; the indexed
  store; random schedules; exporting a schedule as a test; a graphical timeline; a conflict graph; a standalone
  web view (Voilà).

## Alternatives rejected
- A standalone web app: needs hosting and an API to explaindb.
- Explanations computed by the model (built and then removed): they repeated the store's validation logic and
  doubled the code to review.
- A text parser for schedules (built and then removed): the dropdowns create the operations directly.
- A text rendering of the state (built and then removed): only tests used it once the notebook got its own view.
- A validation report produced by the store: it would make `_validate_transaction`, the centrepiece of the teaching
  code, harder to read.
- Undo by rolling the store back: the store cannot do that; re-running is simpler and exact.
- Using the test helpers directly: production code must not import from the tests (the module was moved in 006).

<!-- notes below: skill never overwrites past this marker -->
