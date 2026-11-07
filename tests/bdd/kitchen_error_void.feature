# language: en
@bdd @RF-28 @RF-29 @RF-30 @RF-31
Feature: Kitchen Error Order Void (Voided without Charge)
  As a restaurant chef or administrator
  I want to void an order that failed during cooking
  So that kitchen waste is accounted for and the customer is not charged

  Scenario: Successfully void an in-preparation order by chef
    Given an order exists in "IN_PREPARATION" status with items
    When the chef voids the order via "PATCH /orders/{orderId}/void-kitchen-error" with note "Platillos sobrecocidos y quemados"
    Then the order status becomes "VOIDED"
    And an event "ordenes.comanda.mermada" is staged in the outbox with motive "error_cocina"
    And no billing event is staged in the outbox

  Scenario: Successfully void an in-preparation order by administrator
    Given an order exists in "IN_PREPARATION" status with items
    When the administrator voids the order via "PATCH /orders/{orderId}/void-kitchen-error" with note "Accidente con aceite en cocina"
    Then the order status becomes "VOIDED"
    And an event "ordenes.comanda.mermada" is staged in the outbox with motive "error_cocina"
    And no billing event is staged in the outbox

  Scenario: Reject kitchen error void when explanation note is empty
    Given an order exists in "IN_PREPARATION" status with items
    When the chef attempts to void the order via "PATCH /orders/{orderId}/void-kitchen-error" without note
    Then the system rejects the request with HTTP 422 and error code "NOTE_REQUIRED"
    And the order status remains "IN_PREPARATION"
    And no void event is staged in the outbox

  Scenario: Reject kitchen error void by a waiter
    Given an order exists in "IN_PREPARATION" status with items
    When a waiter attempts to void the order via "PATCH /orders/{orderId}/void-kitchen-error" with note "Intento por mesero"
    Then the system rejects the request with HTTP 403 Forbidden
    And the order status remains "IN_PREPARATION"
    And no void event is staged in the outbox

  Scenario: Reject kitchen error void when order is in RESERVED status
    Given an order exists in "RESERVED" status
    When the chef attempts to void the order via "PATCH /orders/{orderId}/void-kitchen-error" with note "Falta de tiempo"
    Then the system rejects the request with HTTP 409 Conflict
    And the order status remains "RESERVED"
    And no void event is staged in the outbox

