"""Tests for the DB-access lock database.connect()/release_db_lock() added around the
single shared Postgres connection (database.py's _shared_connection) - see connect()'s
own docstring. Two threads sharing that one connection without this lock corrupt pg8000's
wire protocol state instead of erroring cleanly (reproduced manually by firing several
concurrent requests at the onboarding wizard's design step, which loads 5 theme-preview
iframes) - this pins the two properties that fix depends on: same-thread reentrancy
(so a route calling one DB helper from inside another doesn't deadlock on its own lock)
and cross-thread serialization (so two real concurrent requests can't touch the shared
connection at once)."""
import threading

import pytest

import database


@pytest.fixture(autouse=True)
def _ensure_lock_released():
    """Safety net for this file only - a bug in one of these tests leaving the lock
    held would otherwise hang every test that runs after it in the same session
    (connect() blocking forever on a lock nothing will ever release)."""
    yield
    database.release_db_lock()


def test_connect_is_reentrant_within_the_same_thread():
    # The same pattern as a route calling one DB helper function from inside another
    # before the outer one's own connection.close() - must not deadlock against a lock
    # this same thread is already holding.
    database.connect()
    database.connect()
    database.release_db_lock()


def test_release_db_lock_is_safe_when_not_held():
    database.release_db_lock()
    database.release_db_lock()


def test_connect_serializes_across_threads():
    order = []
    first_connected = threading.Event()
    release_first = threading.Event()

    def first():
        database.connect()
        order.append("first_connected")
        first_connected.set()
        release_first.wait(timeout=5)
        database.release_db_lock()
        order.append("first_released")

    def second():
        first_connected.wait(timeout=5)
        database.connect()
        order.append("second_connected")
        database.release_db_lock()

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start()
    t2.start()

    # second's connect() should still be blocked, waiting for first's lock - give it a
    # moment to (incorrectly) race ahead if the lock isn't actually serializing.
    first_connected.wait(timeout=5)
    t2.join(timeout=0.3)
    assert order == ["first_connected"], "second thread's connect() ran before first released the lock"

    release_first.set()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert not t1.is_alive() and not t2.is_alive()
    assert order == ["first_connected", "first_released", "second_connected"]
