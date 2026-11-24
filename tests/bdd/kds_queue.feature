# language: en
@bdd
Feature: KDS Preparation Queue Management
  As a kitchen cook or chef
  I want orders prioritized by elapsed time in kitchen, filtered by station, and stale or paid orders cleared
  So that kitchen staff prepares orders in optimal priority and screen clutter is prevented

  Scenario: Order kitchen queue by elapsed time in kitchen
    Given three active orders exist with different preparation timestamps
    When the kitchen requests the preparation queue via "GET /kds/queue"
    Then the orders are returned sorted by oldest kitchen elapsed time first
    And ties are broken by order creation timestamp

  Scenario: Remove orders from KDS queue when marked as ready and paid
    Given an active order in "IN_PREPARATION" status exists in the kitchen queue
    When the kitchen marks the entire order ready via "PATCH /orders/{orderId}/ready"
    Then the order status becomes "DELIVERED"
    When the order receives payment confirmation and transitions to "PAID"
    And the kitchen requests the preparation queue via "GET /kds/queue"
    Then the paid order is not present in the queue

  Scenario: Filter KDS items by kitchen preparation station
    Given orders exist with items assigned to "grill" and "cold_station" stations
    When the kitchen requests the queue filtered by station "grill" via "GET /kds/queue?station=grill"
    Then only items belonging to station "grill" are included in the results
    And orders with no items for "grill" are omitted

  Scenario: Expired orders appear in Caducados view during grace period
    Given an order is expired within the 10-minute grace window (KDS_EXPIRED_GRACE_MS = 600000)
    When the kitchen requests the preparation queue via "GET /kds/queue"
    Then the expired order is excluded from the active queue
    When an operator requests expired orders via "GET /orders?status=EXPIRED"
    Then the expired order is returned in the list
