# language: en
@bdd
Feature: Inventory Saga and Stock Reservation
  As an order processing system
  I want stock reservations and saga failures to be properly choreographed
  So that inventory consistency is maintained and stale or rejected orders are handled

  Scenario: Successful inventory stock reservation
    Given a confirmed order exists in "CREATED" status awaiting stock reservation
    When inventory confirms stock reservation via "inventario.stock.reservado"
    Then the order status becomes "RESERVED"
    And the order stock_reserved_at timestamp is recorded

  Scenario: Partial inventory stock rejection and reopening to draft
    Given a confirmed order exists in "CREATED" status with 3 requested tacos
    When inventory rejects reservation via "inventario.stock.insuficiente" with availableQuantity 2
    Then the order status becomes "REJECTED"
    And the rejectionDetails contain rejectedItems with availableQuantity 2 and reservedItems
    When the waiter updates the order items via "PATCH /orders/{orderId}/items" with 2 tacos
    Then the order status transitions from "REJECTED" back to "CREATED"
    And the order total amount is updated for 2 items
    When the waiter confirms the order via "POST /orders/{orderId}/confirm"
    Then the order confirmed_at timestamp is recorded
    And a new stock reservation event "ordenes.orden.creada" is staged in the outbox

  Scenario: Kitchen reject with partial availableQuantity in KDS
    Given an order exists in "RESERVED" status with 3 tacos and 1 drink
    When the chef rejects the tacos with availableQuantity 1 via "PATCH /orders/{orderId}/items/{itemId}/kitchen-reject"
    Then the order status becomes "REJECTED"
    And the outbox stages "ordenes.platillo.rechazado_cocina"
    And the rejectionDetails reflect requestedQuantity 3 and availableQuantity 1

  Scenario: Reservation timeout expiration after 15 minutes TTL
    Given a confirmed order in "CREATED" status confirmed 900 seconds ago
    When the saga timeout watcher executes
    Then the order status becomes "EXPIRED"
    And a compensation event "ordenes.orden.expirada" is staged in the outbox

  Scenario: Late stock reservation is ignored on expired orders
    Given an order exists in "EXPIRED" status
    When a late "inventario.stock.reservado" event is received
    Then the late event is ignored
    And the order status remains "EXPIRED"

  Scenario: Late stock reservation is ignored on cancelled orders
    Given an order exists in "CANCELLED" status
    When a late "inventario.stock.reservado" event is received
    Then the late event is ignored
    And the order status remains "CANCELLED"

  Scenario: Expired order in Caducados grace window blocks kitchen preparation and readying
    Given an order exists in "EXPIRED" status within the 10-minute Caducados grace window
    When the kitchen attempts to start preparation via "PATCH /orders/{orderId}/start-preparation"
    Then the system rejects the request with HTTP 409 Conflict
    When the kitchen attempts to mark the order ready via "PATCH /orders/{orderId}/ready"
    Then the system rejects the request with HTTP 409 Conflict
    And the order status remains "EXPIRED"
