# language: en
@bdd
Feature: Order Cancellation
  As a waiter or restaurant admin
  I want to cancel an order while it is in CREATED status
  So that customer cancellations release inventory without generating cooking waste or charges

  Scenario: Successfully cancel an unconfirmed order in CREATED status
    Given an order exists in "CREATED" status without confirmation
    When the waiter requests to cancel the order via "PATCH /orders/{orderId}/cancel"
    Then the order status becomes "CANCELLED"
    And a compensation event "ordenes.orden.cancelada" is staged in the outbox
    And no billing or payment event is emitted

  Scenario: Successfully cancel a confirmed order awaiting inventory stock reservation
    Given an order exists in "CREATED" status with "confirmed_at" timestamp set
    When the waiter requests to cancel the order via "PATCH /orders/{orderId}/cancel"
    Then the order status transitions to "CANCELLED"
    And the compensation event "ordenes.orden.cancelada" contains the cancelled items
    And any subsequent late "inventario.stock.reservado" event is ignored

  Scenario: Reject cancellation when order is already in IN_PREPARATION status
    Given an order exists in "IN_PREPARATION" status
    When the waiter requests to cancel the order via "PATCH /orders/{orderId}/cancel"
    Then the system rejects the request with HTTP 409 Conflict
    And the order status remains "IN_PREPARATION"
    And no cancellation event is staged in the outbox

  Scenario: Reject item modifications once the order has been cancelled
    Given an order has been transitioned to "CANCELLED" status
    When the waiter attempts to update items via "PATCH /orders/{orderId}/items"
    Then the system rejects the request with HTTP 409 Conflict
    And the order items are not modified
