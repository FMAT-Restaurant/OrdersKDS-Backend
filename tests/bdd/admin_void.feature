# language: en
@bdd @RF-26 @RF-27
Feature: Administrative Order Void (Wasted with Charge)
  As a restaurant administrator
  I want to void an order that is already in preparation
  So that kitchen waste is recorded and the customer is charged for the wasted dishes

  Scenario: Successfully void an in-preparation order by administrator
    Given an order exists in "IN_PREPARATION" status with items
    When the administrator voids the order via "PATCH /orders/{orderId}/void"
    Then the order status becomes "WASTED"
    And a billing event "ordenes.cobro.solicitado" is staged in the outbox with frozen prices

  Scenario: Reject administrative void by a waiter
    Given an order exists in "IN_PREPARATION" status with items
    When a waiter attempts to void the order via "PATCH /orders/{orderId}/void"
    Then the system rejects the request with HTTP 403 Forbidden
    And the order status remains "IN_PREPARATION"
    And no void or charge event is staged in the outbox

  Scenario: Reject administrative void when order is in RESERVED status
    Given an order exists in "RESERVED" status
    When the administrator attempts to void the order via "PATCH /orders/{orderId}/void"
    Then the system rejects the request with HTTP 409 Conflict
    And the order status remains "RESERVED"
    And no void or charge event is staged in the outbox

  Scenario: Settle payment for a wasted order
    Given an order exists in "WASTED" status
    When payment completion event "pagos.pago.completado" is processed
    Then the order status transitions to "PAID"

