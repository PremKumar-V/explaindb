# 010 — MVCC playground: notebook with click-built schedules
**Date:** 2026-10
**MR:** https://gitlab.cs.uni-saarland.de/bigdata/dbsys/explaindb/-/merge_requests/143
---
## Context

Feature 009 built the model of the MVCC playground: `run_to` runs the first operations of a schedule on the real
store in one of three modes (validation method 1, method 2, no validation) and returns the versions, the
transactions and the verdict. Students need a notebook (on Binder) in which they build schedules by clicking, load
classic scenarios, switch the mode and run a schedule operation by operation. The model expects a well-formed
schedule (transactions T1..Tn without gaps, each beginning with begin, nothing after its commit or abort) and does
not check it; the verdict tries n! orders for n transactions. The maintainer asked for lean code.

## Decision

- A widget view in a tested module under `system/playground/`; the notebook only bootstraps the import path and
  shows the view ("notebooks thin").
- Only valid operations are offered, so the schedule stays well-formed without a check: a transaction that has
  begun and not ended gets the data operations, commit and abort; the transaction with the lowest number that has
  not begun gets only begin (shown as "T3 (new)"), while fewer than 4 have begun (at most 24 orders for the
  verdict). Removing only the last operation keeps the schedule well-formed. A transaction the store has aborted is
  still offered operations (the choice depends on the schedule alone, the same in every mode); they are skipped.
- Choosing a scenario loads it at once, before its first operation; after an edit the dropdown shows "own
  schedule", so choosing the scenario again reloads it. Eight scenarios, among them the classic write skew of
  `test_write_skew_anomaly` and a write skew through a predicate (insert and delete).
- The editing controls (transaction, operation, key, values, add, remove last) sit in a box apart from the schedule.
- The schedule is a grid of buttons, one row per operation and one column per transaction (headed e.g. "T2 (begin
  ts 3)"); clicking an operation runs the schedule up to and including it, back and forward move by one. Colours
  show what each operation did: ran, planned, skipped, aborted, committed, stopped by an error; the operation that
  ran last is framed. After an error the position stays at the failed operation.
- Values are shown as key and value, e.g. `update x ← (a=1, b=0)`, as the store maps keys to values; timestamps are
  written "ts" throughout.
- Below the grid, in boxes: the store, one row per key with all committed versions (timestamp and writer) and the
  work-in-progress version; the transactions with their status in colour, what they see, read and wrote, and for
  method 1 the checksum of each read when it ran and the one a commit now would compute (red only if the
  transaction wrote, as the store does not validate one that wrote nothing); the verdict in a coloured box.
- One refresh method updates choices, buttons, grid and state after every change, keeps the selected transaction
  and operation when they are still offered, guards against widgets triggering each other again, and closes the
  widgets of the previous grid.
- Tests drive the widgets through their values and button clicks.
- The notebook is listed in the README under "Storage & Data Layout" with a Binder badge, and the tutorial page on
  MVCC points to it.

## Consequences
**Positive:** Students build and replay schedules without typing code or syntax; invalid schedules cannot be built.
The view is tested without a kernel; the notebook stays a few cells.
**Negative:** At most 4 transactions and the keys x, y, z; initial objects are fixed per scenario. Values that do
not apply to the chosen operation are ignored rather than hidden. A delete of a key that does not exist is offered
and ends in the store's error verdict. The colours of the grid are read from the texts of the schedule table's log.
The layout in a browser (CSS, dark theme) is checked by hand only.

## Out of scope & deferred
- Editing the initial objects or using more keys.
- Both methods side by side; a graphical timeline; a conflict graph; exporting a schedule as a test.

## Alternatives rejected
- Typing schedules as text: no value over clicking (the parser was removed in 009).
- `interact()`: cannot keep state across buttons.
- Logic in the notebook: cannot be tested.
- Checking the schedule afterwards instead of offering only valid choices: more code, worse for students.
- Offering only existing keys for a delete: one more rule; the store's error verdict covers it.
- A load button next to the scenario dropdown and a slider over the operations (the first version): unintuitive
  next to a vertical schedule; choosing loads at once, and the grid itself is the control.
- Plain text output: does not show what has run and what is planned; the grid and tables use colours.
- A vertical slider aligned with the rows: cannot be aligned reliably in ipywidgets.
- An `Output` widget for the display: it stays empty without a kernel, so tests cannot read it.

<!-- notes below: skill never overwrites past this marker -->
