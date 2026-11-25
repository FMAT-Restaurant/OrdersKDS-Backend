"""Order domain service orchestrating lifecycle operations and transitions.

Satisfies: RF-09, RF-10, RF-11, RF-16, RF-18, RF-24, RF-25, RF-26, RF-27, RNF-01, RNF-03,
           RNF-09, OP-05, OP-08, OP-11, ADR-0012
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.api.dependencies.auth import AuthContext
from app.api.schemas.order import KitchenRejectItemInput
from app.core.config import get_auth_settings
from app.db.models import Order, OrderItem
from app.db.session import get_session
from app.domain.access import (
    ITEM_CANCEL,
    ITEM_READY,
    ITEM_REDO,
    ITEM_REJECT,
    ORDER_READY,
    REQUEST_BILL,
    START_PREPARATION,
    VOID_ADMIN,
    VOID_KITCHEN_ERROR,
    WILDCARD_PERMISSION,
    AccessPolicy,
)
from app.domain.errors import (
    ForbiddenRoleError,
    InvalidOrderItemError,
    InvalidStateTransitionError,
    NoteRequiredError,
    OrderNotFoundError,
    VersionConflictError,
)
from app.domain.order_status import ItemStatus, OrderStatus
from app.domain.roles import Role, parse_actor_uuid
from app.messaging.events import (
    BillRequestedEvent,
    ChargeRequestedEvent,
    ChargeRequestedItem,
    DishPreparedEvent,
    DishRedoRequestedEvent,
    KitchenDishRejectedEvent,
    OrderCancelledEvent,
    OrderCreatedEvent,
    OrderCreatedItem,
    OrderInPreparationEvent,
    OrderWastedEvent,
)
from app.messaging.validator import validate_event_payload
from app.repositories.orders import OrderRepository
from app.repositories.outbox import OutboxRepository
from app.services.catalog import CatalogValidatorProtocol, get_catalog_validator
from app.services.notifications import (
    OrderNotifier,
    default_notifier,
    notify_item_changed,
    notify_order_changed,
)
from app.services.shifts import ShiftService

logger = logging.getLogger(__name__)


_IDEMPOTENT_CONFIRM_STATUSES = {
    OrderStatus.RESERVED,
    OrderStatus.IN_PREPARATION,
    OrderStatus.DELIVERED,
    OrderStatus.WASTED,
    OrderStatus.PAID,
}

_NON_TRANSFERABLE_STATUSES = {
    OrderStatus.PAID,
    OrderStatus.CANCELLED,
    OrderStatus.REJECTED,
    OrderStatus.EXPIRED,
    OrderStatus.VOIDED,
    OrderStatus.WASTED,
}

_ALLOWED_CANCEL_STATUSES = {
    OrderStatus.CREATED,
    OrderStatus.RESERVED,
    OrderStatus.REJECTED,
}

_NO_REASON_PROVIDED = "No reason provided"


def _has_other_active_items(items: list[OrderItem], excluded_item_id: UUID) -> bool:
    """Return True if at least one non-cancelled item other than excluded_item_id exists."""
    return any(
        item.public_id != excluded_item_id and item.status != ItemStatus.CANCELLED
        for item in items
    )


def _has_undelivered_active_items(items: list[OrderItem]) -> bool:
    """Return True if any non-cancelled item is not DELIVERED or if all items are cancelled."""
    if not items:
        return False
    active_items = [item for item in items if item.status != ItemStatus.CANCELLED]
    if not active_items:
        return True
    return any(item.status != ItemStatus.DELIVERED for item in active_items)


def _consolidate_order_items(items: list[OrderItem]) -> list[OrderCreatedItem]:
    """Group active non-cancelled items by physical recipe and offer IDs and sum quantities."""
    grouped: dict[tuple[str, str], int] = {}
    for item in items:
        if item.status == ItemStatus.CANCELLED or not item.dish_id:
            continue
        effective_offer_id = getattr(item, "offer_id", None) or item.dish_id
        key = (item.dish_id, effective_offer_id)
        grouped[key] = grouped.get(key, 0) + item.quantity

    return [
        OrderCreatedItem(
            offerId=effective_offer_id,
            plateId=dish_id,
            sourceId=dish_id,
            quantity=qty,
        )
        for (dish_id, effective_offer_id), qty in grouped.items()
    ]


def _validate_order_items(items: list[OrderItem]) -> None:
    """Ensure at least one active item exists and each has dish_id and quantity >= 1."""
    active_items = [item for item in items if item.status != ItemStatus.CANCELLED]
    if not active_items:
        raise InvalidOrderItemError("Cannot confirm an order without active items.")
    for item in active_items:
        if item.dish_id is None or item.quantity < 1:
            raise InvalidOrderItemError()


def ensure_waiter_order_ownership(order: Order, auth: AuthContext) -> None:
    """Enforce BOLA ownership so a waiter cannot mutate another waiter's order (OBS-04)."""
    if not get_auth_settings().enforce_waiter_ownership:
        return
    if order.responsible_waiter_id is None:
        return
    if Role.ADMIN in auth.effective_roles or auth.has_permission(WILDCARD_PERMISSION):
        return
    caller_uuid = parse_actor_uuid(auth.user_id)
    if caller_uuid != order.responsible_waiter_id:
        logger.warning(
            "waiter ownership violation on order %s: caller=%s owner=%s",
            order.public_id,
            caller_uuid,
            order.responsible_waiter_id,
        )
        raise ForbiddenRoleError("Cannot modify, transfer, or cancel another waiter's order.")


def _build_charge_items(items: list[OrderItem]) -> list[ChargeRequestedItem]:
    """Build list of charge items with frozen unit price from active non-cancelled items."""
    charge_items: list[ChargeRequestedItem] = []
    for item in items:
        if item.status == ItemStatus.CANCELLED:
            continue
        offer_id = getattr(item, "offer_id", None) or item.dish_id or str(item.public_id)
        charge_items.append(
            ChargeRequestedItem(
                itemId=item.public_id,
                plateId=offer_id,
                name=item.name,
                quantity=item.quantity,
                unitPrice=item.unit_price,
                subtotal=item.unit_price * item.quantity,
                modifiers=list(item.modifiers or []),
                notes=item.notes,
            )
        )
    return charge_items


class OrderService:
    """Service encapsulating order operations, outbox staging, and lifecycle events."""

    def __init__(
        self,
        session: AsyncSession,
        notifier: OrderNotifier | None = None,
        catalog_repo: CatalogValidatorProtocol | None = None,
    ) -> None:
        self._session = session
        self._orders = OrderRepository(session)
        self._outbox = OutboxRepository(session)
        self._notifier = notifier or default_notifier
        self._catalog_repo = catalog_repo

    def _verify_policy(self, auth: AuthContext, policy: AccessPolicy, message: str) -> None:
        if not auth.is_authorized(policy):
            raise ForbiddenRoleError(message)

    async def _commit_or_raise_conflict(self, order_id: UUID) -> None:
        """Commit transaction or rollback and map StaleDataError to 409 VersionConflictError."""
        try:
            await self._session.commit()
        except StaleDataError as exc:
            await self._session.rollback()
            raise VersionConflictError(f"Concurrent update lost for order {order_id}.") from exc

    async def create_order(
        self,
        *,
        table_number: int | None,
        is_takeaway: bool,
        dining_session_id: UUID | None,
        actor_uuid: UUID,
    ) -> Order:
        """Create a new draft order inside the active shift with eager empty items."""
        shift, shift_folio = await ShiftService(self._session).allocate_next_folio_for_order(
            actor_uuid=actor_uuid
        )
        order = Order(
            public_id=uuid4(),
            table_number=table_number,
            is_takeaway=is_takeaway,
            dining_session_id=dining_session_id,
            status=OrderStatus.CREATED,
            created_by=actor_uuid,
            responsible_waiter_id=actor_uuid,
            shift_id=shift.id,
            shift_folio=shift_folio,
            items=[],
        )
        self._orders.save(order)
        await self._commit_or_raise_conflict(order.public_id)
        return order

    def _stage_dish_prepared(
        self,
        order: Order,
        item: OrderItem,
        *,
        is_redo: bool = False,
    ) -> None:
        dish_id = item.dish_id
        raw_offer_id = getattr(item, "offer_id", None)
        effective_offer_id = raw_offer_id or dish_id
        effective_plate_id = dish_id or raw_offer_id
        attempt = item.version if is_redo and (item.version or 0) > 1 else None
        self._outbox.add(
            DishPreparedEvent.for_item(
                order.public_id,
                item.public_id,
                offer_id=effective_offer_id,
                plate_id=effective_plate_id,
                source_id=effective_plate_id,
                quantity=item.quantity,
                is_redo=is_redo,
                attempt=attempt,
            )
        )

    def _sync_delivery_if_all_ready(self, order: Order) -> bool:
        """Move order and all items to DELIVERED if every item is ready/delivered."""
        if not order.items:
            return False
        all_ready = all(
            i.status in (ItemStatus.READY, ItemStatus.DELIVERED, ItemStatus.CANCELLED)
            for i in order.items
        )
        if all_ready and order.status == OrderStatus.IN_PREPARATION:
            order.mark_delivered()
            for item in order.items:
                if item.status == ItemStatus.READY:
                    item.mark_delivered()
            return True
        return False

    async def mark_item_ready(
        self,
        order_id: UUID,
        item_id: UUID,
        auth: AuthContext,
    ) -> Order:
        """Mark an individual order item as READY (RF-09, RF-10, OP-08)."""
        self._verify_policy(
            auth,
            ITEM_READY,
            "Role or permission not allowed for marking item ready.",
        )
        order = await self._orders.get_by_public_id(order_id, for_update=True)
        if order is None:
            raise OrderNotFoundError(f"Order with id={order_id} not found.")

        target_item = next((i for i in order.items if i.public_id == item_id), None)
        if target_item is None:
            raise OrderNotFoundError(f"Item with id={item_id} not found in order {order_id}.")

        if target_item.status in (ItemStatus.READY, ItemStatus.DELIVERED):
            if order.status == OrderStatus.DELIVERED:
                return order
            if order.status != OrderStatus.IN_PREPARATION:
                raise InvalidStateTransitionError(order.status, OrderStatus.DELIVERED)
        elif target_item.status == ItemStatus.PENDING:
            if order.status not in (OrderStatus.IN_PREPARATION, OrderStatus.DELIVERED):
                raise InvalidStateTransitionError(order.status, OrderStatus.DELIVERED)
            is_redo = order.status == OrderStatus.DELIVERED
            target_item.mark_ready()
            self._stage_dish_prepared(
                order,
                target_item,
                is_redo=is_redo,
            )
            if is_redo:
                target_item.mark_delivered()
                order.clear_redo_rejection(target_item.public_id)
                self._restore_pre_redo_note(order, target_item)

        delivered_now = self._sync_delivery_if_all_ready(order)
        self._orders.save(order)
        await self._commit_or_raise_conflict(order_id)

        await notify_item_changed(order, target_item, notifier=self._notifier)
        if delivered_now:
            await notify_order_changed(order, notifier=self._notifier)
        return order

    async def _validate_redo_catalog_availability(self, item: OrderItem) -> None:
        """Verify catalog cache still allows ordering the dish/offer before staging a redo."""
        if self._catalog_repo is None:
            return
        if item.dish_id:
            await self._catalog_repo.validate_dish_available(item.dish_id)
        offer_id = getattr(item, "offer_id", None)
        if offer_id and offer_id != item.dish_id:
            await self._catalog_repo.validate_dish_available(offer_id)

    def _stage_dish_redo_requested(self, order: Order, item: OrderItem, reason: str) -> None:
        """Stage DishRedoRequestedEvent in outbox for Inventory validation and FEFO consumption."""
        raw_offer_id = getattr(item, "offer_id", None)
        effective_plate_id = item.dish_id or raw_offer_id or str(item.public_id)
        effective_offer_id = raw_offer_id or effective_plate_id
        redo_event = DishRedoRequestedEvent.for_item(
            order.public_id,
            item.public_id,
            offer_id=effective_offer_id,
            plate_id=effective_plate_id,
            source_id=effective_plate_id,
            quantity=item.quantity,
            reason=reason,
            attempt=item.version or 1,
        )
        payload = redo_event.model_dump(mode="json", by_alias=True)
        validate_event_payload(redo_event.event_name, payload)
        self._outbox.add(redo_event)

    async def redo_order_item(
        self,
        order_id: UUID,
        item_id: UUID,
        auth: AuthContext,
        *,
        note: str | None,
    ) -> Order:
        """Return one delivered item to PENDING and request Inventory FEFO check (RF-22, OP-12)."""
        self._verify_policy(auth, ITEM_REDO, "Role or permission not allowed for redoing item.")
        normalized_note = note.strip() if note is not None else ""
        if not normalized_note:
            raise NoteRequiredError()

        order = await self._orders.get_by_public_id(order_id, for_update=True)
        if order is None:
            raise OrderNotFoundError(f"Order with id={order_id} not found.")

        target_item = next((i for i in order.items if i.public_id == item_id), None)
        if target_item is None:
            raise OrderNotFoundError(f"Item with id={item_id} not found in order {order_id}.")
        if order.status != OrderStatus.DELIVERED:
            raise InvalidStateTransitionError(order.status, OrderStatus.DELIVERED)

        await self._validate_redo_catalog_availability(target_item)
        order.clear_redo_rejection(target_item.public_id)
        self._save_pre_redo_note(order, target_item)
        target_item.mark_pending_for_redo(normalized_note)
        self._stage_dish_redo_requested(order, target_item, normalized_note)

        self._orders.save(order)
        await self._commit_or_raise_conflict(order_id)
        await notify_item_changed(order, target_item, notifier=self._notifier)
        return order

    async def mark_order_ready(
        self,
        order_id: UUID,
        auth: AuthContext,
    ) -> Order:
        """Mark all pending items of an order as READY and deliver order (RF-11, OP-08)."""
        self._verify_policy(
            auth,
            ORDER_READY,
            "Role or permission not allowed for marking order ready.",
        )
        order = await self._orders.get_by_public_id(order_id, for_update=True)
        if order is None:
            raise OrderNotFoundError(f"Order with id={order_id} not found.")
        if order.status == OrderStatus.DELIVERED:
            return order
        if order.status != OrderStatus.IN_PREPARATION:
            raise InvalidStateTransitionError(order.status, OrderStatus.DELIVERED)

        for item in order.items:
            if item.status == ItemStatus.PENDING:
                item.mark_ready()
                self._stage_dish_prepared(order, item)

        order.mark_delivered()
        for item in order.items:
            if item.status == ItemStatus.READY:
                item.mark_delivered()
                self._restore_pre_redo_note(order, item)

        self._orders.save(order)
        await self._commit_or_raise_conflict(order_id)
        await notify_order_changed(order, notifier=self._notifier)
        return order

    async def start_order_preparation(
        self,
        order_id: UUID,
        auth: AuthContext,
    ) -> Order:
        """Transition order from RESERVED to IN_PREPARATION and publish OrderInPreparationEvent.

        Roles: kitchen, chef, admin.
        Inventory consumes reserved stock and closes reservation TTL upon receiving this event.
        """
        self._verify_policy(
            auth,
            START_PREPARATION,
            "Role or permission not allowed for starting preparation.",
        )
        order = await self._orders.get_by_public_id(order_id, for_update=True)
        if order is None:
            raise OrderNotFoundError(f"Order with id={order_id} not found.")

        if order.status == OrderStatus.IN_PREPARATION:
            return order

        if order.status != OrderStatus.RESERVED:
            raise InvalidStateTransitionError(order.status, OrderStatus.IN_PREPARATION)

        order.mark_in_preparation()
        active_items = _consolidate_order_items(order.items)
        event = OrderInPreparationEvent.for_order(order.public_id, items=active_items)
        payload = event.model_dump(mode="json", by_alias=True)
        validate_event_payload(event.event_name, payload)
        self._outbox.add(event)

        self._orders.save(order)
        await self._commit_or_raise_conflict(order_id)
        await notify_order_changed(order, notifier=self._notifier)
        return order

    async def _stage_kitchen_rejections(
        self,
        order: Order,
        targets: Sequence[tuple[OrderItem, int, str]],
    ) -> None:
        """Disable unavailable catalog entries (qty 0) and stage outbox rejection events."""
        for target_item, effective_available, item_reason in targets:
            dish_id = target_item.dish_id or str(target_item.public_id)
            offer_id = getattr(target_item, "offer_id", None) or dish_id
            if self._catalog_repo is not None and effective_available == 0:
                await self._catalog_repo.set_availability(dish_id, False)
                if offer_id != dish_id:
                    await self._catalog_repo.set_availability(offer_id, False)

            event = KitchenDishRejectedEvent.for_item(
                order_id=order.public_id,
                item_id=target_item.public_id,
                offer_id=offer_id,
                plate_id=dish_id,
                source_id=dish_id,
                quantity=target_item.quantity,
                reason=item_reason,
                attempt=order.version or 1,
            )
            payload = event.model_dump(mode="json", by_alias=True)
            validate_event_payload(event.event_name, payload)
            self._outbox.add(event)

    async def kitchen_reject_item(
        self,
        order_id: UUID,
        item_id: UUID,
        auth: AuthContext,
        reason: str = "Insumo físico insuficiente en cocina",
        available_quantity: int = 0,
        rejected_items: Sequence[KitchenRejectItemInput] | None = None,
    ) -> Order:
        """Kitchen rejects one or more items from a RESERVED order due to lack of stock.

        Updates local Redis catalog availability (available: false when 0 units remain),
        stages KitchenDishRejectedEvent(s) for Inventory/Menu, and transitions order to REJECTED
        with per-item availableQuantity breakdown so the waiter can adjust and re-confirm.
        """
        self._verify_policy(
            auth,
            ITEM_REJECT,
            "Role or permission not allowed for rejecting item.",
        )
        order = await self._orders.get_by_public_id(order_id, for_update=True)
        if order is None:
            raise OrderNotFoundError(f"Order with id={order_id} not found.")

        if order.status not in (OrderStatus.RESERVED, OrderStatus.DELIVERED):
            raise InvalidStateTransitionError(order.status, OrderStatus.REJECTED)

        targets = self._resolve_kitchen_reject_targets(
            order,
            primary_item_id=item_id,
            default_reason=reason,
            default_available=available_quantity,
            rejected_items=rejected_items,
        )
        primary_item = targets[0][0]
        if order.status == OrderStatus.DELIVERED and primary_item.status != ItemStatus.PENDING:
            raise InvalidStateTransitionError(primary_item.status, ItemStatus.CANCELLED)

        await self._stage_kitchen_rejections(order, targets)

        if order.status == OrderStatus.RESERVED:
            order.mark_rejected(
                details=self._build_kitchen_rejection_details(
                    order=order,
                    rejected_targets=targets,
                    default_reason=reason,
                )
            )
        else:
            primary_item.mark_cancelled()
            order.recalculate_total()
            if not _has_other_active_items(order.items, primary_item.public_id):
                order.mark_voided()

        self._orders.save(order)
        await self._commit_or_raise_conflict(order_id)

        for target_item, _, _ in targets:
            await notify_item_changed(order, target_item, notifier=self._notifier)
        await notify_order_changed(order, notifier=self._notifier)
        return order

    async def cancel_item(
        self,
        order_id: UUID,
        item_id: UUID,
        auth: AuthContext,
        reason: str | None = None,
    ) -> Order:
        """Cancel a single item in an order while still in CREATED or RESERVED status."""
        self._verify_policy(
            auth,
            ITEM_CANCEL,
            "Role or permission not allowed for cancelling item.",
        )
        order = await self._orders.get_by_public_id(order_id, for_update=True)
        if order is None:
            raise OrderNotFoundError(f"Order with id={order_id} not found.")

        target_item = next((i for i in order.items if i.public_id == item_id), None)
        if target_item is None:
            raise OrderNotFoundError(f"Item with id={item_id} not found in order {order_id}.")

        is_creatable_or_reserved = order.status in (OrderStatus.CREATED, OrderStatus.RESERVED)
        is_delivered_redo_pending = (
            order.status == OrderStatus.DELIVERED and target_item.status == ItemStatus.PENDING
        )
        if not (is_creatable_or_reserved or is_delivered_redo_pending):
            raise InvalidStateTransitionError(order.status, ItemStatus.CANCELLED)

        if not _has_other_active_items(order.items, target_item.public_id):
            raise InvalidStateTransitionError(order.status, ItemStatus.CANCELLED)

        logger.info(
            "Item %s of order %s cancelled by actor %s (role=%s). Reason: %s",
            item_id,
            order_id,
            auth.user_id,
            auth.role,
            reason or _NO_REASON_PROVIDED,
        )

        target_item.mark_cancelled()
        order.recalculate_total()
        self._orders.save(order)
        await self._commit_or_raise_conflict(order_id)

        await notify_item_changed(order, target_item, notifier=self._notifier)
        await notify_order_changed(order, notifier=self._notifier)
        return order

    async def transfer_order(
        self,
        order_id: UUID,
        new_table_number: int,
        expected_version: int | None = None,
        auth: AuthContext | None = None,
    ) -> Order:
        """Transfer an active order to a different table (RF-16, RNF-09)."""
        order = await self._orders.get_by_public_id(order_id, for_update=True)
        if order is None:
            raise OrderNotFoundError(f"Order with id={order_id} not found.")

        if auth is not None:
            ensure_waiter_order_ownership(order, auth)

        if order.status in _NON_TRANSFERABLE_STATUSES:
            raise InvalidStateTransitionError(order.status, "TRANSFER")

        if expected_version is not None and order.version != expected_version:
            raise VersionConflictError(
                f"Stale version for order {order_id}: "
                f"expected {expected_version}, found {order.version}."
            )

        order.table_number = new_table_number
        order.is_takeaway = False
        if hasattr(self._session, "db"):
            order.version = (order.version or 1) + 1
        self._orders.save(order)

        await self._commit_or_raise_conflict(order_id)

        await notify_order_changed(order, notifier=self._notifier)
        return order

    async def cancel_order(
        self,
        order_id: UUID,
        auth: AuthContext,
        reason: str | None = None,
    ) -> Order:
        """Cancel an order while in CREATED, RESERVED, or REJECTED status (RF-24, RF-25, OP-05)."""
        order = await self._orders.get_by_public_id(order_id, for_update=True)
        if order is None:
            raise OrderNotFoundError(f"Order with id={order_id} not found.")

        ensure_waiter_order_ownership(order, auth)

        if order.status not in _ALLOWED_CANCEL_STATUSES:
            raise InvalidStateTransitionError(order.status, OrderStatus.CANCELLED)

        logger.info(
            "Order %s cancelled by actor %s (role=%s). Reason: %s",
            order.public_id,
            auth.user_id,
            auth.role,
            reason or _NO_REASON_PROVIDED,
        )

        order.mark_cancelled()

        cancelled_items = _consolidate_order_items(order.items)
        event = OrderCancelledEvent.for_order(
            order.public_id,
            items=cancelled_items,
        )
        payload = event.model_dump(mode="json", by_alias=True)
        validate_event_payload(event.event_name, payload)
        self._outbox.add(event)

        self._orders.save(order)
        await self._commit_or_raise_conflict(order_id)

        await notify_order_changed(order, notifier=self._notifier)
        return order

    async def _stage_payment_request(
        self,
        order: Order,
        event_cls: type[ChargeRequestedEvent | BillRequestedEvent],
        *,
        attempt_key: str | int | None = None,
    ) -> None:
        """Stage a charge or bill request event with frozen item prices into the outbox."""
        if order.total_amount is None:
            order.recalculate_total()
        effective_total = order.total_amount if order.total_amount is not None else Decimal("0.00")
        charge_items = _build_charge_items(order.items)
        waiter_id = order.responsible_waiter_id or order.confirmed_by or order.created_by
        table_id = f"MESA-{order.table_number}" if order.table_number is not None else None
        payment_event = event_cls.for_order(
            order.public_id,
            total_amount=effective_total,
            items=charge_items,
            session_id=order.dining_session_id,
            table_id=table_id,
            waiter_id=waiter_id,
            status=order.status.value,
            attempt_key=attempt_key,
        )
        payload = payment_event.model_dump(mode="json", by_alias=True)
        validate_event_payload(payment_event.event_name, payload)
        if not await self._outbox.exists(payment_event.event_id):
            self._outbox.add(payment_event)

    def _stage_order_wasted(
        self,
        order: Order,
        *,
        reason: str,
        motivo: str,
        note: str | None = None,
    ) -> None:
        """Stage an OrderWastedEvent into the outbox for uncharged kitchen or admin waste."""
        wasted_items = _consolidate_order_items(order.items)
        wasted_event = OrderWastedEvent.for_order(
            order.public_id,
            items=wasted_items,
            reason=reason,
            motivo=motivo,
            note=note,
        )
        payload = wasted_event.model_dump(mode="json", by_alias=True)
        validate_event_payload(wasted_event.event_name, payload)
        self._outbox.add(wasted_event)

    async def void_order(
        self,
        order_id: UUID,
        auth: AuthContext,
        reason: str | None = None,
        charge_customer: bool = True,
    ) -> Order:
        """Administratively void an order in IN_PREPARATION status (RF-26, RF-27, ADR-0012).

        Always stages ``ordenes.comanda.mermada`` (``OrderWastedEvent``) so Inventory records
        the kitchen waste movement for consumed ingredients. When ``charge_customer`` is True
        (default per RF-27), transitions the order to ``WASTED`` and stages
        ``ordenes.cobro.solicitado`` (``ChargeRequestedEvent``) in the same database
        transaction. When ``charge_customer`` is False (explicit admin charge waiver, RF-38),
        transitions the order to terminal ``VOIDED`` status without staging a charge event.
        Only users with ``admin`` role or administrative void permission are permitted.
        """
        self._verify_policy(
            auth,
            VOID_ADMIN,
            "Only administrators can administratively void an order.",
        )

        order = await self._orders.get_by_public_id(order_id, for_update=True)
        if order is None:
            raise OrderNotFoundError(f"Order with id={order_id} not found.")

        target_status = OrderStatus.WASTED if charge_customer else OrderStatus.VOIDED
        if order.status != OrderStatus.IN_PREPARATION:
            raise InvalidStateTransitionError(order.status, target_status)

        clean_reason = reason.strip() if reason and reason.strip() else None
        logger.info(
            "Order %s administratively voided (%s, charge=%s) by actor %s (role=%s). Reason: %s",
            order.public_id,
            target_status.value,
            charge_customer,
            auth.user_id,
            auth.role,
            clean_reason or _NO_REASON_PROVIDED,
        )

        if charge_customer:
            order.mark_wasted()
            self._stage_order_wasted(
                order,
                reason="ADMINISTRATIVE",
                motivo="administrativo",
                note=clean_reason,
            )
            await self._stage_payment_request(order, ChargeRequestedEvent)
        else:
            order.mark_voided()
            self._stage_order_wasted(
                order,
                reason="ADMINISTRATIVE",
                motivo="administrativo",
                note=clean_reason,
            )

        self._orders.save(order)
        await self._commit_or_raise_conflict(order_id)

        await notify_order_changed(order, notifier=self._notifier)
        return order

    async def void_kitchen_error(
        self,
        order_id: UUID,
        auth: AuthContext,
        note: str,
    ) -> Order:
        """Void an order in IN_PREPARATION status due to kitchen error (RF-28..31, ADR-0012).

        Transitions the order from IN_PREPARATION to VOIDED (entire order cancelled).
        Publishes 'ordenes.comanda.mermada' with reason='KITCHEN_ERROR', motivo='error_cocina',
        and note. Zero charge is published (no CHARGE_REQUESTED event).
        """
        self._verify_policy(
            auth,
            VOID_KITCHEN_ERROR,
            "Only administrators and chefs can void an order for kitchen error.",
        )

        if not note or not note.strip():
            raise NoteRequiredError("A non-empty note is required for a kitchen error void.")

        order = await self._orders.get_by_public_id(order_id, for_update=True)
        if order is None:
            raise OrderNotFoundError(f"Order with id={order_id} not found.")

        if order.status != OrderStatus.IN_PREPARATION:
            raise InvalidStateTransitionError(order.status, OrderStatus.VOIDED)

        clean_note = note.strip()
        logger.info(
            "Order %s voided for kitchen error (VOIDED) by actor %s (role=%s). Note: %s",
            order.public_id,
            auth.user_id,
            auth.role,
            clean_note,
        )

        order.mark_voided()
        self._stage_order_wasted(
            order,
            reason="KITCHEN_ERROR",
            motivo="error_cocina",
            note=clean_note,
        )

        self._orders.save(order)
        await self._commit_or_raise_conflict(order_id)

        await notify_order_changed(order, notifier=self._notifier)
        return order

    async def request_bill(
        self,
        order_id: UUID,
        auth: AuthContext,
    ) -> Order:
        """Request the bill for an order in DELIVERED status (RF-20, RF-21).

        Requires the order to be in DELIVERED status with all non-cancelled items
        in DELIVERED status (no pending redo items in kitchen).
        Publishes 'ordenes.cuenta.solicitada' with frozen dish prices and modifiers.
        The order remains in DELIVERED status until Payments confirms settlement.
        """
        self._verify_policy(
            auth,
            REQUEST_BILL,
            "Only waiters and administrators can request the bill for an order.",
        )

        order = await self._orders.get_by_public_id(order_id, for_update=True)
        if order is None:
            raise OrderNotFoundError(f"Order with id={order_id} not found.")

        if order.status != OrderStatus.DELIVERED or _has_undelivered_active_items(order.items):
            raise InvalidStateTransitionError(order.status, "REQUEST_BILL")

        logger.info(
            "Bill requested for order %s by actor %s (role=%s)",
            order.public_id,
            auth.user_id,
            auth.role,
        )

        if order.total_amount is None:
            order.recalculate_total()
            self._orders.save(order)
        active_count = sum(1 for item in order.items if item.status != ItemStatus.CANCELLED)
        bill_attempt_key = f"v{order.version or 1}:{order.total_amount}:{active_count}"
        await self._stage_payment_request(
            order,
            BillRequestedEvent,
            attempt_key=bill_attempt_key,
        )

        await self._commit_or_raise_conflict(order_id)

        await notify_order_changed(order, notifier=self._notifier)
        return order

    def _stage_order_confirmation(self, order: Order, confirmed_by: UUID) -> None:
        """Validate items, consolidate quantities, build event and stage to outbox."""
        _validate_order_items(order.items)
        event_items = _consolidate_order_items(order.items)
        attempt_key = order.version if (order.version is not None and order.version > 1) else None
        event = OrderCreatedEvent.for_order(
            order.public_id,
            items=event_items,
            attempt_key=attempt_key,
        )
        payload = event.model_dump(mode="json", by_alias=True)
        validate_event_payload(event.event_name, payload)
        order.confirmed_at = datetime.now(UTC)
        order.confirmed_by = confirmed_by
        self._outbox.add(event)

    async def confirm_order(self, order_id: UUID, confirmed_by: UUID) -> Order:
        """Confirm an order and atomically stage its inventory event (OP-05, TASK-11)."""
        order = await self._orders.get_for_update(order_id)

        if order.status in _IDEMPOTENT_CONFIRM_STATUSES:
            return order

        if order.status != OrderStatus.CREATED:
            raise InvalidStateTransitionError(order.status, "CONFIRM")

        if order.confirmed_at is None:
            await ShiftService(self._session).ensure_shift_open_for_orders(for_update=False)
            self._stage_order_confirmation(order, confirmed_by)
        elif order.confirmed_by is None:
            order.confirmed_by = confirmed_by

        self._orders.save(order)
        await self._commit_or_raise_conflict(order_id)
        return order

    def _save_pre_redo_note(self, order: Order, item: OrderItem) -> None:
        """Persist pre-redo notes in order metadata so completion restores them cleanly."""
        order.save_pre_redo_note(item)

    def _restore_pre_redo_note(self, order: Order, item: OrderItem) -> None:
        """Restore pre-redo preparation notes once kitchen completes a redone item."""
        order.restore_pre_redo_note(item)

    @staticmethod
    def _find_order_item(order: Order, target_id: UUID) -> OrderItem:
        item = next((i for i in order.items if i.public_id == target_id), None)
        if item is None:
            raise OrderNotFoundError(
                f"Item with id={target_id} not found in order {order.public_id}."
            )
        return item

    @staticmethod
    def _clamp_available_quantity(item: OrderItem, raw_available: int) -> int:
        return max(0, min(raw_available, max(0, item.quantity - 1)))

    def _resolve_kitchen_reject_targets(
        self,
        order: Order,
        *,
        primary_item_id: UUID,
        default_reason: str,
        default_available: int,
        rejected_items: Sequence[KitchenRejectItemInput] | None,
    ) -> list[tuple[OrderItem, int, str]]:
        if not rejected_items or order.status == OrderStatus.DELIVERED:
            target = self._find_order_item(order, primary_item_id)
            return [
                (target, self._clamp_available_quantity(target, default_available), default_reason)
            ]

        resolved: dict[UUID, tuple[OrderItem, int, str]] = {}
        for entry in rejected_items:
            target = self._find_order_item(order, entry.item_id)
            resolved[target.public_id] = (
                target,
                self._clamp_available_quantity(target, entry.available_quantity),
                entry.reason or default_reason,
            )
        return list(resolved.values())

    @staticmethod
    def _build_reserved_entry(
        item: OrderItem,
        dish_id: str,
        offer_id: str,
        reserved_quantity: int,
    ) -> dict[str, object]:
        return {
            "itemId": str(item.public_id),
            "offerId": offer_id,
            "dishId": dish_id,
            "name": item.name,
            "reservedQuantity": reserved_quantity,
        }

    def _build_kitchen_rejection_details(
        self,
        *,
        order: Order,
        rejected_targets: Sequence[tuple[OrderItem, int, str]],
        default_reason: str,
    ) -> dict[str, object]:
        """Build structured rejectionDetails payload including multi-item availability."""
        rejected_by_id = {
            target.public_id: (effective_available, item_reason)
            for target, effective_available, item_reason in rejected_targets
        }
        rejected_entries: list[dict[str, object]] = []
        reserved_entries: list[dict[str, object]] = []

        for item in order.items:
            if item.status == ItemStatus.CANCELLED:
                continue
            dish_id = item.dish_id or str(item.public_id)
            offer_id = getattr(item, "offer_id", None) or dish_id
            rejected_info = rejected_by_id.get(item.public_id)
            if rejected_info is None:
                reserved_entries.append(
                    self._build_reserved_entry(item, dish_id, offer_id, item.quantity)
                )
                continue
            effective_available, item_reason = rejected_info
            rejected_entries.append(
                {
                    "itemId": str(item.public_id),
                    "offerId": offer_id,
                    "dishId": dish_id,
                    "sourceId": dish_id,
                    "name": item.name,
                    "requestedQuantity": item.quantity,
                    "availableQuantity": effective_available,
                    "reason": item_reason,
                }
            )
            if effective_available > 0:
                reserved_entries.append(
                    self._build_reserved_entry(item, dish_id, offer_id, effective_available)
                )

        details: dict[str, object] = {
            "source": "kitchen",
            "reason": default_reason,
            "rejectedItems": rejected_entries,
        }
        if reserved_entries:
            details["reservedItems"] = reserved_entries
        return details


def get_order_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    catalog_repo: Annotated[CatalogValidatorProtocol, Depends(get_catalog_validator)],
) -> OrderService:
    """FastAPI dependency yielding an OrderService bound to active session."""
    return OrderService(session, catalog_repo=catalog_repo)
