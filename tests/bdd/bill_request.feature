# language: en
@bdd @RF-20 @RF-21 @RF-04 @RF-18
Feature: Bill Request and Payment Settlement Flow
  As a restaurant waiter or administrator
  I want to request the bill for a delivered order
  So that the customer receives their final bill and can complete payment

  Scenario: Successfully request bill for a delivered order by waiter
    Given an order exists in "DELIVERED" status with items
    When the waiter requests the bill via "POST /orders/{orderId}/request-bill"
    Then the order status remains "DELIVERED"
    And an event "ordenes.cuenta.solicitada" is staged in the outbox
    And the bill event contains frozen items with prices and dual identifiers

  Scenario: Successfully request bill for a delivered order by administrator
    Given an order exists in "DELIVERED" status with items
    When the administrator requests the bill via "POST /orders/{orderId}/request-bill"
    Then the order status remains "DELIVERED"
    And an event "ordenes.cuenta.solicitada" is staged in the outbox

  Scenario: Reject bill request by kitchen role
    Given an order exists in "DELIVERED" status with items
    When a kitchen user attempts to request the bill via "POST /orders/{orderId}/request-bill"
    Then the system rejects the request with HTTP 403 Forbidden
    And the order status remains "DELIVERED"
    And no bill event is staged in the outbox

  Scenario: Reject bill request when order is in IN_PREPARATION status
    Given an order exists in "IN_PREPARATION" status
    When the waiter attempts to request the bill via "POST /orders/{orderId}/request-bill"
    Then the system rejects the request with HTTP 409 Conflict
    And the order status remains "IN_PREPARATION"
    And no bill event is staged in the outbox

  Scenario: Settle payment for a delivered order with requested bill
    Given an order exists in "DELIVERED" status with items
    When payment completion event "pagos.pago.completado" is processed
    Then the order status transitions to "PAID"

