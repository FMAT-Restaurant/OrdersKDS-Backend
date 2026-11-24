# language: en
@bdd
Feature: Redo Delivered Dish
  As a waiter or kitchen chef
  I want to return an individual ruined dish to kitchen with explanatory notes and restore its original recipe notes upon completion
  So that customer food complaints are corrected without voiding the entire delivered order

  Scenario: Waiter requests redo of a delivered dish with mandatory explanation
    Given an order exists in "DELIVERED" status with a delivered dish having original notes "No onions"
    When the waiter requests to redo the dish via "PATCH /orders/{orderId}/items/{itemId}/redo" with note "Customer found onions, please remake"
    Then the order status remains "DELIVERED"
    And the item status becomes "PENDING"
    And the item notes display "Customer found onions, please remake"

  Scenario: Kitchen completes redone dish and restores original notes
    Given an order exists in "DELIVERED" status with a redone dish in "PENDING" status and original notes "No onions"
    When the kitchen marks the redone dish as ready via "PATCH /orders/{orderId}/items/{itemId}/ready"
    Then the item status becomes "DELIVERED"
    And the order status remains "DELIVERED"
    And the dish preparation notes are restored to "No onions"
    And a dish prepared event "ordenes.platillo.preparado" is staged in the outbox with redo flag

  Scenario: Reject redo request when explanation note is blank
    Given an order exists in "DELIVERED" status with a delivered dish
    When the waiter attempts to redo the dish via "PATCH /orders/{orderId}/items/{itemId}/redo" with empty note
    Then the system rejects the request with HTTP 422 and error code "NOTE_REQUIRED"
    And the item status remains "DELIVERED"

  Scenario: Reject marking redone dish ready by a waiter
    Given an order exists in "DELIVERED" status with a redone dish in "PENDING" status
    When a waiter attempts to mark the dish ready via "PATCH /orders/{orderId}/items/{itemId}/ready"
    Then the system rejects the request with HTTP 403 Forbidden
    And the item status remains "PENDING"
