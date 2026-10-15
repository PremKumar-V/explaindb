#
#    This is ExplainDB, educational database systems materials.
#
#    Copyright (C) 2026 Prof. Dr. Jens Dittrich, Saarland University
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
#

# Test modules use self-documenting method/class names and module-level
# fixtures, so pylint's naming and docstring checks are relaxed here.
# This module's schedules resemble the scenarios on purpose (duplicate-code).
# pylint: disable=invalid-name,missing-class-docstring,missing-function-docstring,duplicate-code
"""Tests for the MVCC playground: running schedules operation by operation, showing the store, judging the outcome."""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from system.playground.mvcc_playground import (
    MODES,
    ObjectState,
    RunState,
    StoreWithoutValidation,
    TransactionState,
    Verdict,
    Version,
    WorkInProgress,
    make_store,
    run_to,
)
from system.playground.mvcc_scenarios import SCENARIOS, Scenario, schedule_of
from system.playground.schedules import (
    DataOperation,
    Row,
    Schedule,
    StoredObject,
)
from system.stores.MVCC import TransactionalKeyValueStore

# x is selected by a == 1:
INITIAL_X: tuple[StoredObject, ...] = (StoredObject("x", Row(1, 0)),)


def scenario_named(name: str) -> Scenario:
    """Returns the scenario of SCENARIOS with the given name."""
    return next(s for s in SCENARIOS if s.name == name)


def statuses(result: RunState) -> tuple[str, ...]:
    """Returns the status of every transaction, e.g. ``("committed", "aborted")`` for T1, T2."""
    return tuple(t.status for t in result.transactions)


class MakeStoreTest(unittest.TestCase):
    """Creating the store for a mode."""

    def test_store_per_mode(self) -> None:
        """Method 1 re-runs every read at the commit, method 2 checks the reads against the objects written by
        transactions that committed meanwhile, and "no validation" does not validate.

        Would fail if a mode were mapped to the wrong validation, so the playground showed another method than
        the one selected.
        """
        method_1: TransactionalKeyValueStore = make_store("method 1")
        method_2: TransactionalKeyValueStore = make_store("method 2")
        self.assertNotIsInstance(method_1, StoreWithoutValidation)
        self.assertTrue(method_1.use_brute_force_validation)
        self.assertNotIsInstance(method_2, StoreWithoutValidation)
        self.assertFalse(method_2.use_brute_force_validation)
        self.assertIsInstance(make_store("no validation"), StoreWithoutValidation)


@dataclass(frozen=True)
class ExpectedOutcome:
    """The outcome of a scenario run to the end: transaction statuses and verdict kind, with and without
    validation (method 1 and method 2 agree in every scenario)."""

    validated: tuple[str, ...]
    validated_verdict: str
    unvalidated: tuple[str, ...]
    unvalidated_verdict: str


# the statuses of the transactions, and the verdict kinds: serializable in the order of the serialization points
# (SAME), only in another order (OTHER), or not at all (NOT). The serialization point of a transaction that wrote
# is its commit timestamp: validation methods 1 and 2 check its reads against the state at that timestamp. A
# read-only transaction also gets a commit timestamp, but it skips the validation and reads only the snapshot of
# its begin, so its serialization point is its begin (its TA_id). Commit timestamps and TA_ids come from the same
# counter, so they can be compared. The verdict first replays the committed transactions in this order; aborted
# transactions are left out (see TransactionRecord.serialization_point and serial_order):
C: str = "committed"
A: str = "aborted"
SAME: str = "serializable"
OTHER: str = "serializable in another order"
NOT: str = "not serializable"

# the outcome of every scenario:
EXPECTED_OUTCOMES: dict[str, ExpectedOutcome] = {
    "serial": ExpectedOutcome((C, C), SAME, (C, C), SAME),
    "lost update": ExpectedOutcome((C, A), SAME, (C, C), NOT),
    "write skew": ExpectedOutcome((C, A), SAME, (C, C), NOT),
    "phantom insert": ExpectedOutcome((A, C), SAME, (C, C), OTHER),
    "concurrent delete": ExpectedOutcome((A, C), SAME, (C, C), OTHER),
    "read-only transaction": ExpectedOutcome((C, C), SAME, (C, C), SAME),
    "read-only anomaly (Fekete et al.)": ExpectedOutcome(
        (C, A, C), SAME, (C, C, C), NOT
    ),
}


class ScenarioOutcomeTest(unittest.TestCase):
    """Every scenario run to the end in every mode."""

    def test_outcome_and_verdict_per_mode(self) -> None:
        """Each scenario ends with the pinned transaction statuses and verdict kind in each mode.

        Would fail if a validation method let an anomaly commit, aborted a transaction needlessly, if the store
        without validation stopped showing the anomalies, or if the verdict misjudged a run.
        """
        for s in SCENARIOS:
            expected: ExpectedOutcome = EXPECTED_OUTCOMES[s.name]
            for mode in MODES:
                with self.subTest(scenario=s.name, mode=mode):
                    result: RunState = run_to(
                        s.schedule, s.initial_state, mode, len(s.schedule)
                    )
                    validated: bool = mode != "no validation"
                    self.assertEqual(
                        expected.validated if validated else expected.unvalidated,
                        statuses(result),
                    )
                    self.assertEqual(
                        (
                            expected.validated_verdict
                            if validated
                            else expected.unvalidated_verdict
                        ),
                        result.verdict.kind,
                    )


class VerdictTest(unittest.TestCase):
    """The verdict's sentence for the anomalies the store without validation lets through."""

    def test_serializable_in_another_order(self) -> None:
        """The phantom insert without validation is serializable, but in the order T1, T2, not T2, T1.

        ```text
        T1                          T2
        begin
        read a==1 -> {x}
                                    begin
                                    update y=Row(1,0)
                                    commit
        update z=Row(0,1)
        commit
        ```

        Would fail if the verdict stopped after the order of the serialization points or did not name the order
        that matches and why the other one does not.
        """
        s: Scenario = scenario_named("phantom insert")
        result: RunState = run_to(
            s.schedule, s.initial_state, "no validation", len(s.schedule)
        )
        self.assertEqual(
            Verdict(
                "serializable in another order",
                "Not like the order T2, T1 (T1: read a==1 returned {x=Row(1,0)}, in the serial order it returns "
                "{x=Row(1,0), y=Row(1,0)}), but the same read results and final state as the serial order T1, T2: "
                "serializable.",
            ),
            result.verdict,
        )

    def test_not_serializable(self) -> None:
        """The write skew without validation is not serializable; the verdict names the first difference.

        ```text
        T1                          T2
        begin
                                    begin
        read a==1 -> {x}
                                    read a==1 -> {x}
        update y=Row(1,0)
                                    delete x
        commit
                                    commit
        ```

        Would fail if some order were wrongly accepted or the verdict left out the difference in the order of the
        serialization points.
        """
        s: Scenario = scenario_named("write skew")
        result: RunState = run_to(
            s.schedule, s.initial_state, "no validation", len(s.schedule)
        )
        self.assertEqual(
            Verdict(
                "not serializable",
                "No serial order gives the same read results and final state (T1, T2: T2: read a==1 returned "
                "{x=Row(1,0)}, in the serial order it returns {x=Row(1,0), y=Row(1,0)}): not serializable.",
            ),
            result.verdict,
        )


class RunToTest(unittest.TestCase):
    """Running the first operations of a schedule."""

    def test_empty_schedule(self) -> None:
        """An empty schedule shows the initial objects, no transactions and the verdict "pending".

        Would fail if the empty schedule got a final verdict before a transaction was added.
        """
        empty: RunState = run_to((), INITIAL_X, "method 2", 0)
        self.assertEqual("pending", empty.verdict.kind)
        self.assertEqual((ObjectState("x", (Version(0, Row(1, 0)),)),), empty.objects)
        self.assertEqual((), empty.transactions)

    def test_transaction_that_aborts_on_its_own(self) -> None:
        """A transaction that aborts on its own leaves no version behind; with nothing committed, the verdict says
        so.

        ```text
        T1
        begin
        update x=Row(2,0)
        abort
        ```

        Would fail if the abort left the work-in-progress version in the store, or if a run in which every
        transaction aborted were judged as serializable or kept pending.
        """
        schedule: Schedule = schedule_of(
            (1, "begin"), (1, DataOperation("update", "x", Row(2, 0))), (1, "abort")
        )
        after_update: RunState = run_to(schedule, INITIAL_X, "method 2", 2)
        self.assertEqual(
            ObjectState("x", (Version(0, Row(1, 0)),), WorkInProgress("T1", Row(2, 0))),
            after_update.objects[0],
        )
        at_end: RunState = run_to(schedule, INITIAL_X, "method 2", len(schedule))
        self.assertEqual((ObjectState("x", (Version(0, Row(1, 0)),)),), at_end.objects)
        self.assertEqual(
            Verdict("nothing committed", "Nothing committed."), at_end.verdict
        )

    def test_verdict_is_final_once_every_transaction_has_ended(self) -> None:
        """The verdict is final as soon as every transaction has ended, even if skipped operations follow.

        ```text
        T1                          T2
        begin
                                    begin
        update x=Row(2,0)
                                    update x=Row(3,0) -> aborted
        commit                                        <- operation 5: every transaction has ended
                                    commit -> skipped
        ```

        Would fail if the verdict waited for the last operation of the schedule instead of the end of the last
        transaction, or if it were given while T1 is still running after operation 4.
        """
        schedule: Schedule = schedule_of(
            (1, "begin"),
            (2, "begin"),
            (1, DataOperation("update", "x", Row(2, 0))),
            (2, DataOperation("update", "x", Row(3, 0))),
            (1, "commit"),
            (2, "commit"),
        )
        after_4: RunState = run_to(schedule, INITIAL_X, "method 2", 4)
        after_5: RunState = run_to(schedule, INITIAL_X, "method 2", 5)
        at_end: RunState = run_to(schedule, INITIAL_X, "method 2", len(schedule))
        self.assertEqual("pending", after_4.verdict.kind)
        self.assertEqual(
            Verdict(
                "serializable",
                "Same read results and final state as the serial order T1: serializable.",
            ),
            after_5.verdict,
        )
        self.assertEqual(at_end.verdict, after_5.verdict)

    def test_object_states_of_concurrent_delete(self) -> None:
        """After T2's delete, while T2 is still running, x has a work-in-progress delete marker; after T2's commit,
        a committed delete marker.

        ```text
        T1                          T2
        begin
                                    begin
        read a==1 -> {x}
                                    delete x       <- operation 4
                                    commit
        update z=Row(0,1)
        commit -> aborts
        ```

        Would fail if a work-in-progress delete were shown as an update or without its owner, or if a committed
        delete were not shown as a version of its own.
        """
        s: Scenario = scenario_named("concurrent delete")
        at_delete: RunState = run_to(s.schedule, s.initial_state, "method 2", 4)
        self.assertEqual(
            ObjectState("x", (Version(0, Row(1, 0)),), WorkInProgress("T2", None)),
            at_delete.objects[0],
        )
        at_end: RunState = run_to(
            s.schedule, s.initial_state, "method 2", len(s.schedule)
        )
        self.assertEqual(
            ObjectState("x", (Version(0, Row(1, 0)), Version(3, None))),
            at_end.objects[0],
        )


class ErrorTest(unittest.TestCase):
    """A run that cannot continue."""

    def test_delete_of_key_whose_update_was_skipped(self) -> None:
        """A delete of a key whose only update was skipped stops the run with the store's KeyError as its verdict.

        ```text
        T1                          T2
        begin
                                    begin
        update x=Row(2,0)
                                    update x=Row(3,0) -> aborted
                                    update y=Row(1,0) -> skipped
                                    commit -> skipped
        delete y -> error
        commit -> not run
        ```

        Would fail if the store's KeyError escaped run_to, the error did not name y, the failed delete were not the
        last line of the table, or the verdict were judged on the unfinished run.
        """
        schedule: Schedule = schedule_of(
            (1, "begin"),
            (2, "begin"),
            (1, DataOperation("update", "x", Row(2, 0))),
            (2, DataOperation("update", "x", Row(3, 0))),
            (2, DataOperation("update", "y", Row(1, 0))),
            (2, "commit"),
            (1, DataOperation("delete", "y")),
            (1, "commit"),
        )
        for mode in MODES:
            with self.subTest(mode=mode):
                result: RunState = run_to(schedule, INITIAL_X, mode, len(schedule))
                self.assertEqual(7, len(result.log))
                self.assertEqual("delete y: raised KeyError: 'y'", result.log[-1].text)
                self.assertEqual(
                    Verdict(
                        "error", "The run stopped: delete y: raised KeyError: 'y'."
                    ),
                    result.verdict,
                )


class TransactionStateTest(unittest.TestCase):
    """What each transaction did so far and what it sees, in the write skew scenario with method 2.

    ```text
    T1                          T2
    begin
                                begin
    read a==1 -> {x}
                                read a==1 -> {x}
    update y=Row(1,0)
                                delete x
    commit
                                commit -> aborts
    ```
    """

    def test_transaction_not_begun_yet(self) -> None:
        """After T1's begin, T2 has not begun: it has no id and shows nothing.

        Would fail if a transaction that has not begun were left out, or shown with an id or a snapshot.
        """
        s: Scenario = scenario_named("write skew")
        result: RunState = run_to(s.schedule, s.initial_state, "method 2", 1)
        self.assertEqual(
            TransactionState("T2", None, "not begun", (), (), ()),
            result.transactions[1],
        )

    def test_work_in_progress_insert_and_running_writer(self) -> None:
        """While T1's insert of y runs (after operation 5), y has no committed version, and T1 sees y and lists its
        read and write.

        Would fail if an object without committed version were left out, if the work-in-progress version did not
        name its writer, or if a running transaction did not see its own write.
        """
        s: Scenario = scenario_named("write skew")
        result: RunState = run_to(s.schedule, s.initial_state, "method 2", 5)
        self.assertEqual(
            ObjectState("y", (), WorkInProgress("T1", Row(1, 0))), result.objects[1]
        )
        self.assertEqual(
            TransactionState(
                "T1",
                1,
                "running",
                (StoredObject("x", Row(1, 0)), StoredObject("y", Row(1, 0))),
                ("a==1",),
                ("y",),
            ),
            result.transactions[0],
        )

    def test_aborted_transaction_keeps_its_read_and_write(self) -> None:
        """T2 aborts at its commit and still lists what it read and wrote, but sees nothing any more.

        Would fail if the read and the write that made T2 abort disappeared with the abort (the store forgets an
        aborted transaction).
        """
        s: Scenario = scenario_named("write skew")
        result: RunState = run_to(
            s.schedule, s.initial_state, "method 2", len(s.schedule)
        )
        self.assertEqual(
            TransactionState("T2", 2, "aborted", (), ("a==1",), ("x",)),
            result.transactions[1],
        )


if __name__ == "__main__":
    unittest.main()
