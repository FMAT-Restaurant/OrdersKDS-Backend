"""Publisher protocol, adapter and in-memory event bus implementation for contract stubs.

Satisfies: RNF-13
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol
from uuid import UUID

from app.messaging.publisher import EventPublisher
from app.messaging.validator import validate_event_payload


class StubPublisherProtocol(Protocol):
    """Protocol satisfied by real RabbitMQ publishers or in-memory stub test buses."""

    async def publish(
        self,
        event_type: str,
        payload: Mapping[str, object],
        event_id: UUID,
        correlation_id: UUID | None = None,
        headers: Mapping[str, object] | None = None,
        exchange: str | None = None,
    ) -> None:
        """Publish an event with validation against contract JSON schemas."""
        ...


class StubEventPublisherAdapter:
    """Adapts application EventPublisher to StubPublisherProtocol."""

    def __init__(self, publisher: EventPublisher) -> None:
        self._publisher = publisher

    async def publish(
        self,
        event_type: str,
        payload: Mapping[str, object],
        event_id: UUID,
        correlation_id: UUID | None = None,
        headers: Mapping[str, object] | None = None,
        exchange: str | None = None,
    ) -> None:
        await self._publisher.publish(
            event_type=event_type,
            payload=payload,
            event_id=event_id,
            correlation_id=correlation_id,
        )

    async def close(self) -> None:
        """Close underlying connection."""
        await self._publisher.close()


class InMemoryStubPublisher:
    """In-memory event publisher collecting emitted events for unit/integration testing."""

    def __init__(self) -> None:
        self.published_events: list[dict[str, Any]] = []

    async def publish(
        self,
        event_type: str,
        payload: Mapping[str, object],
        event_id: UUID,
        correlation_id: UUID | None = None,
        headers: Mapping[str, object] | None = None,
        exchange: str | None = None,
    ) -> None:
        """Validate against JSON schema and stage event in memory."""
        validate_event_payload(event_type=event_type, payload=payload)
        record = {
            "event_type": event_type,
            "payload": dict(payload),
            "event_id": event_id,
            "correlation_id": correlation_id,
            "headers": dict(headers) if headers else {},
            "exchange": exchange,
        }
        self.published_events.append(record)

    def get_events(self, event_type: str | None = None) -> list[dict[str, Any]]:
        """Return published events, optionally filtered by event_type."""
        if event_type is None:
            return list(self.published_events)
        return [e for e in self.published_events if e["event_type"] == event_type]

    def clear(self) -> None:
        """Clear published events history."""
        self.published_events.clear()
