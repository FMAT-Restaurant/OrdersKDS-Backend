"""Floor & Dining Room contract stub simulating table opening and waiter assignments.

Architecture & Language Convention Note:
-----------------------------------------
1. Inter-Team Convention:
   All cross-microservice communication is agreed to be in English:
   - ``sessionId``, ``tableId``, ``waiterId``, ``dinersCount``

2. Sibling Implementation Discrepancy:
   Inspection of `Floor-Module` (`packages/shared/src/events/index.ts` and
   `apps/api/src/events/rabbitmq.publisher.ts`) revealed that their codebase defines
   Spanish field names inside an envelope:
   - ``mesaId`` (number | string)
   - ``meseroId`` (string)
   - ``comensales`` (number)
   - ``unionId`` (string | null)
   - ``mesasIds`` (array of table identifiers)
   - Nested inside an ``EventEnvelope<T>`` object with key ``data``.
   - AMQP headers: ``x-event-id`` and ``x-correlation-id``.

   To bridge this discrepancy, FloorStub supports the agreed English fields canonically while
   also populating the Spanish fields in `data`, ensuring full compatibility with both the
   inter-team convention and the real Floor-Module implementation.

Satisfies: RF-17, RNF-13, OP-11
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.stubs.config import StubLanguageConvention, StubSettings
from app.stubs.protocol import StubPublisherProtocol

logger = logging.getLogger(__name__)

EVENT_SALA_DINING_SESSION_INICIADA = "sala.dining_session.iniciada"


class FloorStub:
    """Executable contract stub for Floor / Salón microservice."""

    def __init__(
        self,
        publisher: StubPublisherProtocol,
        settings: StubSettings | None = None,
    ) -> None:
        self.publisher = publisher
        self.settings = settings or StubSettings()
        self.emitted_sessions: dict[str, dict[str, Any]] = {}

    async def emit_session_started(
        self,
        session_id: UUID | str,
        table_id: str | int = "MESA_1",
        waiter_id: UUID | str | None = None,
        diners_count: int = 2,
        union_id: str | None = None,
        joined_table_ids: list[str | int] | None = None,
        correlation_id: UUID | None = None,
        event_id: UUID | None = None,
    ) -> UUID:
        """Emit a dining session started event.

        Args:
            session_id: Unique session identifier.
            table_id: Identifier of table (supports strings or numbers like Floor-Module).
            waiter_id: Attending waiter identifier.
            diners_count: Headcount at the table.
            union_id: Optional joined tables group identifier.
            joined_table_ids: Optional list of table ids when tables are grouped.
            correlation_id: Optional tracing identifier.
            event_id: Optional fixed event UUID for idempotency replay testing.

        Returns:
            The event_id used to publish the event.
        """
        chosen_event_id = event_id or uuid4()
        chosen_correlation_id = correlation_id or uuid4()
        session_str = str(session_id)
        waiter_str = str(waiter_id or uuid4())
        now_iso = datetime.now(UTC).isoformat()

        # Build data payload addressing the Floor-Module language discrepancy
        data_payload: dict[str, Any] = {
            "sessionId": session_str,
            "timestamp": now_iso,
        }

        # Language convention mapping
        if self.settings.language_convention in (
            StubLanguageConvention.ENGLISH,
            StubLanguageConvention.HYBRID,
        ):
            data_payload["tableId"] = table_id
            data_payload["waiterId"] = waiter_str
            data_payload["dinersCount"] = diners_count

        if self.settings.language_convention in (
            StubLanguageConvention.SPANISH_LEGACY,
            StubLanguageConvention.HYBRID,
        ):
            data_payload["mesaId"] = table_id
            data_payload["meseroId"] = waiter_str
            data_payload["comensales"] = diners_count
            data_payload["numComensales"] = diners_count
            data_payload["unionId"] = union_id
            data_payload["mesasIds"] = joined_table_ids

        # Construct Floor-Module EventEnvelope<T>
        envelope: dict[str, Any] = {
            "eventId": str(chosen_event_id),
            "eventType": EVENT_SALA_DINING_SESSION_INICIADA,
            "timestamp": now_iso,
            "correlationId": str(chosen_correlation_id),
            "data": data_payload,
        }

        # Also provide top-level access for pure English consumers
        envelope["sessionId"] = session_str
        envelope["tableId"] = table_id
        envelope["waiterId"] = waiter_str
        envelope["dinersCount"] = diners_count

        headers = {
            "x-event-id": str(chosen_event_id),
            "x-correlation-id": str(chosen_correlation_id),
        }

        await self.publisher.publish(
            event_type=EVENT_SALA_DINING_SESSION_INICIADA,
            payload=envelope,
            event_id=chosen_event_id,
            correlation_id=chosen_correlation_id,
            headers=headers,
            exchange="sala.events",
        )

        self.emitted_sessions[session_str] = envelope
        logger.info(
            "FloorStub: emitted session started for %s (table %s, waiter %s)",
            session_str,
            table_id,
            waiter_str,
        )
        return chosen_event_id

    async def emit_duplicate_session_started(
        self,
        session_id: UUID | str,
    ) -> UUID:
        """Replay an identical event to verify consumer idempotency."""
        session_str = str(session_id)
        if session_str not in self.emitted_sessions:
            raise KeyError(f"Session {session_str} has not been emitted yet")

        envelope = self.emitted_sessions[session_str]
        event_id = UUID(envelope["eventId"])
        correlation_id = UUID(envelope["correlationId"]) if envelope.get("correlationId") else None

        await self.publisher.publish(
            event_type=EVENT_SALA_DINING_SESSION_INICIADA,
            payload=envelope,
            event_id=event_id,
            correlation_id=correlation_id,
            headers={
                "x-event-id": str(event_id),
                "x-correlation-id": str(correlation_id) if correlation_id else "",
            },
            exchange="sala.events",
        )
        logger.info("FloorStub: emitted DUPLICATE session event for %s", session_str)
        return event_id
