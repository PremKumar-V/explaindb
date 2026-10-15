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

"""A playground for schedules on the MVCC store: run them operation by operation, show the store, judge the outcome.

The model runs a schedule (see :mod:`system.playground.schedules`) on the real store and shows, after any operation,
the versions in the store and what each transaction sees, read and wrote.
"""

# The model reads the store's internals (versions, snapshots) on purpose, to show them.
# pylint: disable=protected-access

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Literal

from system.playground.schedules import (
    LogLine,
    Row,
    Schedule,
    ScheduleRun,
    StoredObject,
    TransactionRecord,
    run_schedule,
    serial_order,
    serializability_violation,
)
from system.stores.MVCC import TransactionalKeyValueStore
from system.stores.VersionedKeyValueStore import VersionedKeyValueStore

# how the store validates a transaction at its commit:
Mode = Literal["method 1", "method 2", "no validation"]
MODES: tuple[Mode, ...] = ("method 1", "method 2", "no validation")


class StoreWithoutValidation(TransactionalKeyValueStore):
    """A store that never validates: it gives snapshot isolation only, so anomalies such as write skew happen."""

    def _validate_transaction(
        self, TA_id: int, commit_timestamp_for_this_TA: int
    ) -> bool:
        """See :meth:`TransactionalKeyValueStore._validate_transaction`.

        Always passes, so no conflict is ever detected.
        """
        return True


def make_store(mode: Mode) -> TransactionalKeyValueStore:
    """Returns an empty store that validates according to ``mode``.

    @param mode: one of :data:`MODES`
    @return: a store with validation method 1 or 2, or a :class:`StoreWithoutValidation`
    """
    if mode == "no validation":
        return StoreWithoutValidation()
    return TransactionalKeyValueStore(use_brute_force_validation=mode == "method 1")


# ---------------------------------------------------------------------------------------------------------------
# the state after some operations


@dataclass(frozen=True)
class Version:
    """A committed version of an object: its commit timestamp and its value (None for a delete marker)."""

    commit_timestamp: int
    row: Row | None


@dataclass(frozen=True)
class WorkInProgress:
    """The work-in-progress version of an object: the transaction writing it and the value (None for a delete)."""

    owner: str
    row: Row | None


@dataclass(frozen=True)
class ObjectState:
    """The versions of one object in the store."""

    object_id: str
    committed: tuple[Version, ...]
    # the version a running transaction is writing, None if there is none:
    work_in_progress: WorkInProgress | None = None


@dataclass(frozen=True)
class TransactionState:
    """What one transaction did so far and what it sees."""

    name: str
    # the transaction id the store gave it at its begin, None before its begin:
    TA_id: int | None
    status: Literal["not begun", "running", "committed", "aborted"]
    # what a read of all objects returns for the transaction (its snapshot plus its own writes); empty unless running:
    sees: tuple[StoredObject, ...]
    # the where clauses of its reads, e.g. a==1 or all, and the ids of the objects it wrote, each once and sorted
    # (an aborted transaction keeps them, so that one can see what it did):
    read_clauses: tuple[str, ...]
    write_set: tuple[str, ...]


@dataclass(frozen=True)
class Verdict:
    """The judgement of a run, as a kind and as a sentence: pending, error, nothing committed, or whether the
    committed transactions behaved like some serial order."""

    kind: Literal[
        "pending",
        "error",
        "nothing committed",
        "serializable",
        "serializable in another order",
        "not serializable",
    ]
    text: str


@dataclass(frozen=True)
class RunState:
    """The state after running the first operations of a schedule."""

    objects: tuple[ObjectState, ...]
    transactions: tuple[TransactionState, ...]
    # the schedule table's lines, one per operation run (also a skipped one and, after an error, the one that failed):
    log: tuple[LogLine, ...]
    verdict: Verdict


def object_states(
    store: TransactionalKeyValueStore, names: dict[int, str]
) -> tuple[ObjectState, ...]:
    """Returns the versions of every object in the store, sorted by object id.

    @param names: the names of the transactions (T1, T2, ...) by their transaction id
    """
    states: list[ObjectState] = []
    # sorted, so that the objects appear in the same order in every run:
    for object_id in sorted(store.key_value_store.keys()):
        entry: VersionedKeyValueStore.KVStoreEntry = store.key_value_store[object_id]
        # the committed versions in commit order; a committed version starts at its commit timestamp (0 for an
        # initial object), and a delete marker has no value:
        committed: tuple[Version, ...] = tuple(
            Version(
                version.start_validity,
                (
                    None
                    if isinstance(version, VersionedKeyValueStore.DeleteEntry)
                    else version.value
                ),
            )
            for version in entry.committed
        )
        wip: VersionedKeyValueStore.VersionEntry | None = entry.wip
        work_in_progress: WorkInProgress | None = None
        if wip is not None:
            # a work-in-progress version carries the transaction id of its writer as its start:
            work_in_progress = WorkInProgress(
                names[wip.start_validity],
                (
                    wip.value
                    if isinstance(wip, VersionedKeyValueStore.UpdateEntry)
                    else None
                ),
            )
        states.append(ObjectState(object_id, committed, work_in_progress))
    return tuple(states)


def transaction_states(
    store: TransactionalKeyValueStore, records: list[TransactionRecord]
) -> tuple[TransactionState, ...]:
    """Returns the state of every transaction of the schedule."""
    states: list[TransactionState] = []
    for record in records:
        # a transaction that has not begun has no transaction id and nothing to show yet:
        if record.TA_id is None:
            states.append(TransactionState(record.name, None, "not begun", (), (), ()))
            continue
        status: Literal["running", "committed", "aborted"]
        if record.committed:
            status = "committed"
        elif record.aborted:
            status = "aborted"
        else:
            status = "running"
        # the record keeps every read and every write that ran, also after an abort (a write the store refused
        # did not run and is not in it); a read that ran twice is listed once:
        read_clauses: tuple[str, ...] = tuple(
            sorted(
                {
                    (
                        "all"
                        if done.data_operation.a_equals is None
                        else f"a=={done.data_operation.a_equals}"
                    )
                    for done in record.data_operations
                    if done.data_operation.kind == "read"
                }
            )
        )
        write_set: tuple[str, ...] = tuple(
            sorted(
                {
                    done.data_operation.key
                    for done in record.data_operations
                    if done.data_operation.kind != "read"
                }
            )
        )
        sees: tuple[StoredObject, ...] = ()
        if status == "running":
            # what a read of all objects returns ([0]: the (object_id, object)-pairs, without the checksum); unlike
            # the store's read_objects, this does not add a read to the transaction's read set:
            sees = tuple(
                StoredObject(object_id, row)
                for object_id, row in sorted(store._read_snapshot(record.TA_id)[0])
            )
        states.append(
            TransactionState(
                record.name, record.TA_id, status, sees, read_clauses, write_set
            )
        )
    return tuple(states)


# ---------------------------------------------------------------------------------------------------------------
# judging


def serializability_verdict(
    initial_state: dict[str, Row],
    records: list[TransactionRecord],
    final_state: dict[str, Row],
) -> Verdict:
    """Judges whether the committed transactions behaved like some serial order.

    Compares the read results and the final state with a serial replay, first in the order of the serialization
    points (the commit timestamp of a transaction that wrote, the begin of a read-only one), then in every other
    order of the committed transactions (n! orders for n committed transactions).
    """
    # the committed transactions in the order of their serialization points (aborted ones had no effect):
    committed: list[TransactionRecord] = serial_order(records)
    if not committed:
        return Verdict("nothing committed", "Nothing committed.")
    order_text: str = ", ".join(record.name for record in committed)
    # first the order of the serialization points, the order validation methods 1 and 2 aim for:
    violation_in_serialization_order: str | None = serializability_violation(
        initial_state, records, final_state
    )
    if violation_in_serialization_order is None:
        return Verdict(
            "serializable",
            f"Same read results and final state as the serial order {order_text}: serializable.",
        )
    # otherwise the schedule may still be serializable in some other order:
    for order in itertools.permutations(committed):
        # the order of the serialization points was already replayed above:
        if list(order) == committed:
            continue
        if (
            serializability_violation(initial_state, records, final_state, list(order))
            is None
        ):
            return Verdict(
                "serializable in another order",
                f"Not like the order {order_text} ({violation_in_serialization_order}), but the same read results "
                f"and final state as the serial order {', '.join(record.name for record in order)}: "
                "serializable.",
            )
    # no serial order explains what the transactions read and what the store holds at the end:
    return Verdict(
        "not serializable",
        f"No serial order gives the same read results and final state ({order_text}: "
        f"{violation_in_serialization_order}): not serializable.",
    )


# ---------------------------------------------------------------------------------------------------------------
# running


def run_to(
    schedule: Schedule,
    initial_state: tuple[StoredObject, ...],
    mode: Mode,
    operation_count: int,
) -> RunState:
    """Runs the first ``operation_count`` operations of ``schedule`` on a fresh store and returns the state after them.

    The state after any operation is found by running the schedule again from its start, as the store cannot return
    to an earlier state; runs are deterministic, so this gives the same state every time.

    @param schedule: a well-formed schedule (not checked): transactions T1, T2, ... without gaps, each of which
        begins before its other operations and runs nothing after its commit or abort; with n transactions, the
        verdict may replay n! orders
    @param initial_state: the objects in the store before the first operation, each object id once
    @param mode: how the store validates
    @param operation_count: how many operations to run, from 0; a larger number runs the whole schedule
    @return: the state after the operations, with the verdict ("pending" until every transaction has ended, "error"
        if the store raised an error other than an abort, e.g. a KeyError for a delete of an object it does not have)
    """
    store: TransactionalKeyValueStore = make_store(mode)
    # put writes the initial objects as committed versions with timestamp 0, before any transaction:
    for stored in initial_state:
        store.put(stored.object_id, stored.row)
    run: ScheduleRun = run_schedule(store, schedule, operation_count)
    # the verdict is only meaningful once every transaction has ended; an empty schedule stays pending:
    verdict: Verdict = Verdict("pending", "Pending: not every transaction has ended.")
    if run.error is not None:
        # TODO(#53): revisit once deleting an object that is not visible raises KeyError (see TODO.md)
        verdict = Verdict("error", f"The run stopped: {run.error}.")
    elif run.records and all(
        record.committed or record.aborted for record in run.records
    ):
        verdict = serializability_verdict(
            {stored.object_id: stored.row for stored in initial_state},
            run.records,
            run.final_state,
        )
    # the names of the transactions by the id the store gave them, to name the writer of a version:
    names: dict[int, str] = {
        record.TA_id: record.name for record in run.records if record.TA_id is not None
    }
    return RunState(
        objects=object_states(store, names),
        transactions=transaction_states(store, run.records),
        log=tuple(run.log),
        verdict=verdict,
    )
