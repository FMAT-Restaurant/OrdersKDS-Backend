"""Unit tests for the Saga Timeout Watcher.

Satisfies: RF-35, RNF-01, RNF-02, UT-BE-50, UT-BE-51
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import SagaTimeoutSettings
from app.db.models import Order, Outbox
from app.domain.order_status import OrderStatus
from app.messaging.events import EventName, OrderExpiredEvent
from app.workers.saga_timeout_watcher import Clock, SagaTimeoutWatcher, Sleeper

_NOW = datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC)
_TIMEOUT_SECONDS = 60


class InMemoryDb:
    """Minimal database double holding orders and staged outbox rows."""

    def __init__(self, orders: list[Order] | None = None) -> None:
        self.orders: list[Order] = orders or []
        self.outbox: list[Outbox] = []


class _ScalarResult:
    def __init__(self, items: list[Order]) -> None:
        self._items = items

    def __iter__(self) -> Iterator[Order]:
        return iter(self._items)


class FakeSession:
    """Session double supporting the repository calls used by the watcher."""

    def __init__(self, db: InMemoryDb) -> None:
        self.db = db
        self.commit_count = 0

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def scalars(self, _statement: object) -> _ScalarResult:
        # Return every order: the watcher re-checks the boundary predicate in
        # Python, which is exactly what UT-BE-50 exercises.
        return _ScalarResult(self.db.orders)

    def add(self, instance: object) -> None:
        if isinstance(instance, Outbox):
            self.db.outbox.append(instance)

    async def commit(self) -> None:
        self.commit_count += 1


def _make_order(
    *,
    status: OrderStatus = OrderStatus.CREATED,
    confirmed_at: datetime | None = None,
    created_at: datetime | None = None,
) -> Order:
    return Order(
        public_id=uuid4(),
        status=status,
        confirmed_at=confirmed_at,
        created_at=created_at or _NOW,
    )


def _make_watcher(
    db: InMemoryDb,
    *,
    clock: Clock = lambda: _NOW,
    sleeper: Sleeper = asyncio.sleep,
) -> SagaTimeoutWatcher:
    def session_factory() -> AsyncSession:
        return FakeSession(db)  # type: ignore[return-value]

    return SagaTimeoutWatcher(
        session_factory,
        settings=SagaTimeoutSettings(
            timeout_seconds=_TIMEOUT_SECONDS,
            watcher_interval_seconds=5.0,
        ),
        sleeper=sleeper,
        clock=clock,
    )


@pytest.mark.unit
@pytest.mark.requirement("RF-35", "OP-01")
@pytest.mark.parametrize(
    ("elapsed_seconds", "should_expire"),
    [
        (59, False),
        (60, True),
        (61, True),
    ],
)
@pytest.mark.asyncio
async def test_saga_watcher_expires_only_orders_older_than_threshold(
    elapsed_seconds: int,
    should_expire: bool,
) -> None:
    """UT-BE-50: boundary values 59 s, 60 s and 61 s from confirmed_at."""
    order = _make_order(confirmed_at=_NOW - timedelta(seconds=elapsed_seconds))
    db = InMemoryDb([order])
    watcher = _make_watcher(db)

    expired = await watcher.expire_stale_orders()

    assert expired == int(should_expire)
    assert order.status == (OrderStatus.EXPIRED if should_expire else OrderStatus.CREATED)
    assert len(db.outbox) == int(should_expire)


@pytest.mark.unit
@pytest.mark.requirement("RF-35", "OP-01")
@pytest.mark.asyncio
async def test_saga_watcher_never_expires_draft_without_confirmed_at() -> None:
    """A CREATED draft may sit for hours while the waiter assembles it."""
    order = _make_order(
        confirmed_at=None,
        created_at=_NOW - timedelta(hours=3),
    )
    db = InMemoryDb([order])
    watcher = _make_watcher(db)

    expired = await watcher.expire_stale_orders()

    assert expired == 0
    assert order.status == OrderStatus.CREATED
    assert db.outbox == []


@pytest.mark.unit
@pytest.mark.requirement("RF-35")
@pytest.mark.parametrize(
    "status",
    [
        OrderStatus.IN_PREPARATION,
        OrderStatus.EXPIRED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.PAID,
    ],
)
@pytest.mark.asyncio
async def test_saga_watcher_ignores_orders_no_longer_in_created(status: OrderStatus) -> None:
    order = _make_order(status=status, confirmed_at=_NOW - timedelta(minutes=10))
    db = InMemoryDb([order])
    watcher = _make_watcher(db)

    expired = await watcher.expire_stale_orders()

    assert expired == 0
    assert order.status == status
    assert db.outbox == []


@pytest.mark.unit
@pytest.mark.requirement("RF-35", "RNF-02")
@pytest.mark.asyncio
async def test_saga_watcher_publishes_order_expired_exactly_once() -> None:
    """UT-BE-51: two consecutive scans stage a single ORDER_EXPIRED row."""
    order = _make_order(confirmed_at=_NOW - timedelta(seconds=61))
    db = InMemoryDb([order])
    watcher = _make_watcher(db)

    first_scan = await watcher.expire_stale_orders()
    second_scan = await watcher.expire_stale_orders()

    assert (first_scan, second_scan) == (1, 0)
    assert order.status == OrderStatus.EXPIRED
    assert len(db.outbox) == 1

    row = db.outbox[0]
    assert row.event_type == EventName.ORDER_EXPIRED
    assert row.payload["orderId"] == str(order.public_id)
    assert row.payload["elapsedSeconds"] == 61
    assert row.event_id == OrderExpiredEvent.for_order(order.public_id).event_id


@pytest.mark.unit
@pytest.mark.requirement("OP-01")
def test_saga_watcher_fails_when_timeout_variable_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SAGA_TIMEOUT_SECONDS", raising=False)

    def session_factory() -> AsyncSession:
        return FakeSession(InMemoryDb())  # type: ignore[return-value]

    with pytest.raises(
        RuntimeError,
        match="Missing required environment variable: SAGA_TIMEOUT_SECONDS",
    ):
        SagaTimeoutWatcher(session_factory)


@pytest.mark.unit
@pytest.mark.requirement("RF-35")
@pytest.mark.asyncio
async def test_saga_watcher_run_scans_and_sleeps_until_stopped() -> None:
    stop_event = asyncio.Event()
    delays: list[float] = []

    async def sleeper(delay: float) -> None:
        delays.append(delay)
        stop_event.set()

    order = _make_order(confirmed_at=_NOW - timedelta(seconds=60))
    db = InMemoryDb([order])
    watcher = _make_watcher(db, sleeper=sleeper)

    await watcher.run(stop_event=stop_event)

    assert delays == [5.0]
    assert order.status == OrderStatus.EXPIRED
    assert len(db.outbox) == 1


@pytest.mark.unit
@pytest.mark.requirement("RF-35", "RNF-01")
@pytest.mark.asyncio
async def test_saga_watcher_run_backs_off_after_failures() -> None:
    stop_event = asyncio.Event()
    delays: list[float] = []
    calls = 0

    async def sleeper(delay: float) -> None:
        delays.append(delay)

    async def failing_scan() -> int:
        nonlocal calls
        calls += 1
        if calls >= 3:
            stop_event.set()
        raise RuntimeError("database unavailable")

    watcher = _make_watcher(InMemoryDb(), sleeper=sleeper)
    watcher.expire_stale_orders = failing_scan  # type: ignore[method-assign]

    await watcher.run(stop_event=stop_event)

    assert delays == [1.0, 2.0, 4.0]


@pytest.mark.unit
@pytest.mark.requirement("RF-35")
@pytest.mark.asyncio
async def test_saga_watcher_run_handles_cancellation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def cancelled_scan() -> int:
        raise asyncio.CancelledError()

    watcher = _make_watcher(InMemoryDb())
    watcher.expire_stale_orders = cancelled_scan  # type: ignore[method-assign]

    with caplog.at_level(logging.INFO), pytest.raises(asyncio.CancelledError):
        await watcher.run()

    assert "Saga Timeout Watcher received cancellation" in caplog.text


def test_order_expired_event_id_is_stable_per_order() -> None:
    order_id: UUID = uuid4()

    first = OrderExpiredEvent.for_order(order_id).event_id
    second = OrderExpiredEvent.for_order(order_id).event_id

    assert first == second
