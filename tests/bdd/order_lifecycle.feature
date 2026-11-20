# language: en
@bdd
Feature: Order Complete Lifecycle
  As a restaurant system
  I want an order to progress across its full operational lifecycle
  So that customer requests flow cleanly from creation to kitchen delivery

  Scenario: Complete happy path from draft creation to kitchen delivery
    When the waiter creates a table order draft via "POST /orders"
    Then the order status becomes "CREATED"
    And the created_by user matches the authenticated waiter
    And the active shift has issued folio 1
    When the waiter adds 2 items via "PATCH /orders/{orderId}/items"
    Then the order contains 2 items
    When the waiter confirms the order via "POST /orders/{orderId}/confirm"
    Then the order confirmed_at timestamp is recorded
    And the stock reservation event "ordenes.orden.creada" is staged in the outbox
    And the stock reservation event "ordenes.orden.creada" includes plateId and sourceId
    When inventory confirms stock reservation via "inventario.stock.reservado"
    Then the order status becomes "RESERVED"
    When the kitchen starts preparation via "PATCH /orders/{orderId}/start-preparation"
    Then the order status becomes "IN_PREPARATION"
    And the preparation event "ordenes.orden.en_preparacion" is staged in the outbox
    When the kitchen marks item 1 as ready via "PATCH /orders/{orderId}/items/{itemId}/ready"
    Then item 1 status becomes "READY"
    And the order status remains "IN_PREPARATION"
    And a dish prepared event "ordenes.platillo.preparado" is staged in the outbox
    And the outbox dish prepared events include plateId and sourceId
    When the kitchen marks item 2 as ready via "PATCH /orders/{orderId}/items/{itemId}/ready"
    Then item 2 status becomes "DELIVERED"
    And the order status becomes "DELIVERED"
    And a dish prepared event "ordenes.platillo.preparado" is staged in the outbox

  Scenario: Create takeaway order draft via API
    When the waiter creates a takeaway order draft via "POST /orders"
    Then the order status becomes "CREATED"
    And the order is marked as takeaway
    And the created_by user matches the authenticated waiter

  Scenario: Dining session started event creates draft order idempotently
    When a dining session started event "sala.dining_session.iniciada" is received
    Then a new order is created in "CREATED" status linked to the dining session
    When the same dining session started event is redelivered
    Then the duplicate event is ignored without creating another order

  Scenario: Idempotent order confirmation by waiter
    Given an order exists in "CREATED" status with "confirmed_at" timestamp set
    When the waiter confirms the order again via "POST /orders/{orderId}/confirm"
    Then the system accepts the request with HTTP 200 OK
    And the order status remains "CREATED"
    And no duplicate "ordenes.orden.creada" event is staged in the outbox

  Scenario: Modify draft items prior to confirmation
    Given a draft order exists in "CREATED" status with 1 item
    When the waiter updates the order items via "PATCH /orders/{orderId}/items" with 2 new items
    Then the order status remains "CREATED"
    And the order contains 2 items
    And the order total amount is recalculated

  Scenario: Reject empty item replacement on draft order
    Given a draft order exists in "CREATED" status with 1 item
    When the waiter attempts to update items with an empty items list via "PATCH /orders/{orderId}/items"
    Then the system rejects the request with HTTP 422 Unprocessable Entity
    And the order items are not modified

  Scenario: Reject item modifications once the order has been confirmed
    Given an order exists in "CREATED" status with "confirmed_at" timestamp set
    When the waiter attempts to update items via "PATCH /orders/{orderId}/items"
    Then the system rejects the request with HTTP 409 Conflict
    And the order items are not modified
