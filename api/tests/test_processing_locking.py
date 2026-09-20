"""Lock ordering in the processing task.

The two sides of a card are processed by two workers at the same time and both write rows that
belong to the same inventory item. In Postgres a child insert takes a `FOR KEY SHARE` lock on
the parent, so a later `FOR UPDATE` on that same parent is a lock *upgrade* — and two
transactions upgrading the same row deadlock. Postgres kills one, and the side it kills loses
its processed image silently: the capture looks fine and the job is simply gone.

This is a race, so a unit test cannot reproduce it reliably. What it can do is pin the property
that prevents it: the exclusive lock is taken before anything that writes to the item. If a
future edit moves a write above the lock, that ordering breaks and this fails.
"""

import inspect

from app.services import processing


def _line_of(source: str, needle: str) -> int:
    for n, line in enumerate(source.splitlines()):
        if needle in line:
            return n
    raise AssertionError(f"not found in source: {needle}")


def test_process_image_locks_before_it_writes():
    source = inspect.getsource(processing.process_image)
    lock = _line_of(source, "_lock_item(session, item)")
    # `_store_processed` is the first thing that inserts a row referencing the item.
    write = _line_of(source, "_store_processed(")
    assert lock < write, (
        "the item must be locked before the first child write, or two workers "
        "processing the two sides of one card will deadlock on a lock upgrade"
    )


def test_manual_corners_path_locks_before_it_writes():
    source = inspect.getsource(processing.process_with_corners)
    lock = _line_of(source, "_lock_item(session, item)")
    write = _line_of(source, "_store_processed(")
    assert lock < write


def test_reconcile_still_takes_the_lock_for_callers_that_did_not():
    """Re-requesting a lock the transaction already holds is free, so this stays."""
    assert "_lock_item(session, item)" in inspect.getsource(processing._reconcile_review)


def test_the_lock_is_exclusive():
    """`FOR UPDATE`, not a shared lock — a shared one is what deadlocked."""
    assert "with_for_update()" in inspect.getsource(processing._lock_item)
