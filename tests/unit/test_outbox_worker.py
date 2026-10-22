"""Unit tests for the Event Outbox Worker.

Satisfies: RNF-01, RNF-06, RNF-12, UT-BE-48, UT-BE-49
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Outbox
from app.messaging.events import EventName
from app.messaging.publisher import EventPublishError
from app.workers.outbox_worker import OutboxWorker


def _make_outbox_row(
    event_type: str = EventName.ORDER_CREATED,
    created_at_offset_seconds: float = 2.0,
) -> Outbox:
    """Helper to build a sample Outbox ORM model instance."""
    event_id = uuid4()
    order_id = uuid4()
    row = Outbox(
        event_id=event_id,
        event_type=event_type,
        payload={"eventId": str(event_id), "orderId": str(order_id), "correlationId": None},
        correlation_id=uuid4(),
    )
    row.id = 1
    row.created_at = datetime.now(UTC) - timedelta(seconds=created_at_offset_seconds)
    row.published_at = None
    return row


@pytest.mark.unit
@pytest.mark.requirement("RNF-01", "RNF-06", "RNF-12")
@pytest.mark.asyncio
async def test_outbox_worker_publishes_pending_rows_and_marks_them_published(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """UT-BE-48: pending rows are published in order, marked published, and lag is logged."""
    fixed_time = datetime(2026, 10, 22, 12, 0, 0, tzinfo=UTC)
    row1 = _make_outbox_row(EventName.ORDER_CREATED)
    row1.id = 1
    row1.created_at = fixed_time - timedelta(milliseconds=2500)

    row2 = _make_outbox_row(EventName.DISH_PREPARED)
    row2.id = 2
    row2.created_at = fixed_time - timedelta(milliseconds=1200)

    pending_rows = [row1, row2]

    read_session = AsyncMock(spec=AsyncSession)
    read_session.scalars.return_value = pending_rows

    write_session1 = AsyncMock(spec=AsyncSession)
    write_session2 = AsyncMock(spec=AsyncSession)
    write_sessions = [write_session1, write_session2]

    @asynccontextmanager
    async def session_factory() -> AsyncIterator[AsyncMock]:
        if not write_sessions:
            yield read_session
        else:
            if read_session.scalars.await_count == 0:
                yield read_session
            else:
                yield write_sessions.pop(0)

    mock_publisher = AsyncMock()

    worker = OutboxWorker(
        session_factory=session_factory,
        publisher=mock_publisher,
        clock=lambda: fixed_time,
    )

    with caplog.at_level(logging.INFO):
        count = await worker.process_batch(limit=10)

    assert count == 2
    assert mock_publisher.publish.await_count == 2

    # Verify FIFO publishing order
    first_call = mock_publisher.publish.await_args_list[0]
    second_call = mock_publisher.publish.await_args_list[1]
    assert first_call.kwargs["event_id"] == row1.event_id
    assert first_call.kwargs["event_type"] == EventName.ORDER_CREATED
    assert second_call.kwargs["event_id"] == row2.event_id
    assert second_call.kwargs["event_type"] == EventName.DISH_PREPARED

    # Verify each write session executed mark_published and committed
    write_session1.execute.assert_awaited_once()
    write_session1.commit.assert_awaited_once()
    write_session2.execute.assert_awaited_once()
    write_session2.commit.assert_awaited_once()

    # Verify deterministic lag calculation and structured extra dictionary
    assert "with lag 2500 ms" in caplog.text
    assert "with lag 1200 ms" in caplog.text

    records = [r for r in caplog.records if r.name == "app.workers.outbox_worker"]
    assert len(records) == 2
    assert records[0].lag_ms == 2500
    assert records[0].event_id == str(row1.event_id)
    assert records[1].lag_ms == 1200


@pytest.mark.unit
@pytest.mark.requirement("RNF-01")
@pytest.mark.asyncio
async def test_outbox_worker_keeps_row_pending_when_publish_fails() -> None:
    """UT-BE-49: if publish confirm fails, the row is not marked published and stays pending."""
    row = _make_outbox_row(EventName.ORDER_CREATED)
    pending_rows = [row]

    read_session = AsyncMock(spec=AsyncSession)
    read_session.scalars.return_value = pending_rows

    write_session = AsyncMock(spec=AsyncSession)

    @asynccontextmanager
    async def session_factory() -> AsyncIterator[AsyncMock]:
        if read_session.scalars.await_count == 0:
            yield read_session
        else:
            yield write_session

    mock_publisher = AsyncMock()
    mock_publisher.publish.side_effect = EventPublishError("Simulated broker NACK or restart")

    worker = OutboxWorker(
        session_factory=session_factory,
        publisher=mock_publisher,
    )

    with pytest.raises(EventPublishError, match="Simulated broker NACK or restart"):
        await worker.process_batch(limit=10)

    # DoD requirement: row must NOT be marked or committed
    write_session.commit.assert_not_awaited()
    assert row.published_at is None


@pytest.mark.unit
@pytest.mark.requirement("RNF-01", "RNF-06")
@pytest.mark.asyncio
async def test_outbox_worker_dod_retry_recovers_after_broker_failure() -> None:
    """DoD: Attempt 1 fails -> row stays pending -> Attempt 2 succeeds and marks row."""
    row = _make_outbox_row(EventName.ORDER_CREATED)

    read_session1 = AsyncMock(spec=AsyncSession)
    read_session1.scalars.return_value = [row]
    read_session2 = AsyncMock(spec=AsyncSession)
    read_session2.scalars.return_value = [row]
    write_session = AsyncMock(spec=AsyncSession)

    session_sequence = [read_session1, write_session, read_session2, write_session]

    @asynccontextmanager
    async def session_factory() -> AsyncIterator[AsyncMock]:
        yield session_sequence.pop(0)

    mock_sleeper = AsyncMock()
    mock_publisher = AsyncMock()
    mock_publisher.publish.side_effect = [
        EventPublishError("Broker restarting"),
        None,
    ]

    worker = OutboxWorker(
        session_factory=session_factory,
        publisher=mock_publisher,
        base_backoff_seconds=1.0,
        sleeper=mock_sleeper,
    )

    # Attempt 1: Fails
    with pytest.raises(EventPublishError, match="Broker restarting"):
        await worker.process_batch(limit=10)

    assert row.published_at is None
    write_session.commit.assert_not_awaited()

    # Attempt 2: Succeeds without event loss
    count = await worker.process_batch(limit=10)
    assert count == 1
    write_session.execute.assert_awaited_once()
    write_session.commit.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.requirement("RNF-01", "RNF-06")
@pytest.mark.asyncio
async def test_outbox_worker_run_retries_with_exponential_backoff() -> None:
    """Verify worker loop executes backoff when batches repeatedly fail."""
    mock_sleeper = AsyncMock()
    stop_event = asyncio.Event()

    worker = OutboxWorker(
        session_factory=AsyncMock(),
        publisher=AsyncMock(),
        base_backoff_seconds=1.0,
        max_backoff_seconds=10.0,
        sleeper=mock_sleeper,
    )

    call_count = 0

    async def failing_batch(_: int = 100) -> int:
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            stop_event.set()
        raise EventPublishError("Broker unavailable")

    worker.process_batch = failing_batch  # type: ignore[method-assign]

    await worker.run(stop_event=stop_event)

    assert mock_sleeper.await_count == 3
    delays = [call.args[0] for call in mock_sleeper.await_args_list]
    assert delays == [1.0, 2.0, 4.0]


@pytest.mark.unit
@pytest.mark.requirement("RNF-01")
@pytest.mark.asyncio
async def test_outbox_worker_run_handles_cancellation() -> None:
    """Verify clean loop exit on asyncio.CancelledError."""
    worker = OutboxWorker(
        session_factory=AsyncMock(),
        publisher=AsyncMock(),
    )

    async def cancelled_batch(_: int = 100) -> int:
        raise asyncio.CancelledError()

    worker.process_batch = cancelled_batch  # type: ignore[method-assign]

    await worker.run()


@pytest.mark.unit
@pytest.mark.requirement("RNF-01")
@pytest.mark.asyncio
async def test_outbox_worker_empty_batch_sleeps_poll_interval() -> None:
    """Verify worker sleeps for poll_interval when no rows are pending."""
    mock_sleeper = AsyncMock()
    stop_event = asyncio.Event()

    worker = OutboxWorker(
        session_factory=AsyncMock(),
        publisher=AsyncMock(),
        poll_interval_seconds=3.0,
        sleeper=mock_sleeper,
    )

    async def empty_batch(_: int = 100) -> int:
        stop_event.set()
        return 0

    worker.process_batch = empty_batch  # type: ignore[method-assign]

    await worker.run(stop_event=stop_event)
    mock_sleeper.assert_awaited_once_with(3.0)


@pytest.mark.unit
@pytest.mark.requirement("RNF-01")
@pytest.mark.asyncio
async def test_outbox_worker_run_processes_batch_and_continues_when_count_positive() -> None:
    """Verify worker.run() executes the count > 0 branch without sleeping poll interval."""
    stop_event = asyncio.Event()
    worker = OutboxWorker(session_factory=AsyncMock(), publisher=AsyncMock())

    call_count = 0

    async def positive_batch(_: int = 100) -> int:
        nonlocal call_count
        call_count += 1
        stop_event.set()
        return 2

    worker.process_batch = positive_batch  # type: ignore[method-assign]
    await worker.run(stop_event=stop_event)
    assert call_count == 1


@pytest.mark.unit
@pytest.mark.requirement("RNF-01")
@pytest.mark.asyncio
async def test_outbox_worker_process_batch_returns_zero_when_no_pending_rows() -> None:
    read_session = AsyncMock(spec=AsyncSession)
    read_session.scalars.return_value = []

    @asynccontextmanager
    async def session_factory() -> AsyncIterator[AsyncMock]:
        yield read_session

    worker = OutboxWorker(
        session_factory=session_factory,
        publisher=AsyncMock(),
    )
    count = await worker.process_batch(limit=10)
    assert count == 0


@pytest.mark.unit
@pytest.mark.requirement("RNF-01")
def test_run_all_database_url_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers.run_all import _get_database_url

    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL environment variable is required"):
        _get_database_url()

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/db")
    assert _get_database_url() == "postgresql+asyncpg://user:pass@localhost:5432/db"


@pytest.mark.unit
@pytest.mark.requirement("RNF-01", "RNF-06")
@pytest.mark.asyncio
async def test_run_all_main_lifecycle(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from app.workers.run_all import main

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/db")
    monkeypatch.setenv("RABBITMQ_URL", "amqp://user:pass@localhost:5672/")
    monkeypatch.setenv("RABBITMQ_EXCHANGE", "test.topic")
    monkeypatch.setenv("RABBITMQ_VHOST", "/")

    mock_engine = AsyncMock()
    mock_publisher = AsyncMock()

    captured_handlers: list[Callable[[], None]] = []

    def mock_add_signal_handler(sig: int, callback: Callable[[], None]) -> None:
        captured_handlers.append(callback)

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "add_signal_handler", mock_add_signal_handler)

    async def fake_run(*_: object, stop_event: asyncio.Event | None = None) -> None:
        assert stop_event is not None
        if captured_handlers:
            captured_handlers[0]()
            assert stop_event.is_set()

    with (
        patch("app.workers.run_all.create_async_engine", return_value=mock_engine),
        patch("app.workers.run_all.EventPublisher", return_value=mock_publisher),
        patch("app.workers.outbox_worker.OutboxWorker.run", fake_run),
        caplog.at_level(logging.INFO),
    ):
        await main()

    mock_publisher.connect.assert_awaited_once()
    mock_publisher.close.assert_awaited_once()
    mock_engine.dispose.assert_awaited_once()
    assert "Shutdown signal received" in caplog.text
