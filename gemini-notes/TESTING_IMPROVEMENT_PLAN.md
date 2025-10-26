# Testing Improvement Plan for `petrosa-tradeengine`

## 1. Executive Summary

This document outlines a significant gap between the complexity of the `tradeengine`'s core logic, particularly in `dispatcher.py`, and the superficiality of its corresponding test suite. The current tests do not adequately verify the system's correctness or its critical safety features, creating significant operational risk.

The implementation includes robust, production-grade features like distributed locking, duplicate signal rejection, risk management, and complex OCO (One-Cancels-the-Other) order management. However, the tests for these features are either non-existent or are simple "happy path" checks that mock out the very logic that needs to be tested.

This plan proposes a new, multi-layered testing strategy and provides clear guidelines for writing meaningful unit and integration tests to ensure the trading engine behaves as expected and is safe for production use.

## 2. Analysis of the Current Test Suite (`test_dispatcher.py`)

### 2.1. Key Findings

*   **Superficial Coverage:** Tests provide a false sense of security. They confirm that methods can be called without crashing but do not validate the business logic within them.
*   **Over-reliance on Mocking:** Core components are mocked to the point that the tests are not testing the actual behavior of the dispatcher, but rather the behavior of the mocks.
*   **Critical Features Are Untested:** The following essential features have **zero effective test coverage**:
    *   **Duplicate Signal Rejection:** No test ensures that if a signal is received twice, it is only processed once.
    *   **Distributed Locking:** No test simulates a lock being acquired or failing, which is critical for preventing race conditions in a distributed deployment.
    *   **Risk Management:** No test verifies that an order is rejected if it violates position or loss limits.
    *   **OCO Order Logic:** The entire `OCOManager`—responsible for placing, monitoring, and canceling stop-loss/take-profit orders—is untested.
*   **Lack of Integration Testing:** There are no tests that verify the `Dispatcher`'s interaction with its collaborators (`PositionManager`, `OrderManager`, `Exchange`), which is where many bugs originate.

## 3. Proposed Testing Strategy & Guidelines

A more robust testing strategy is required. We recommend a multi-layered approach focusing heavily on integration testing for the `Dispatcher`.

### 3.1. Unit Test Guidelines

Unit tests should be reserved for simple, pure functions or classes with minimal dependencies.

*   **Target:** Private helper functions like `_signal_to_order()` and `_generate_signal_id()`.
*   **Guidelines:**
    *   Test a variety of inputs, including valid data, edge cases (e.g., zero or negative values), and invalid data.
    *   Assert the return values are correct.
    *   **Example:** Write a test for `_generate_signal_id()` that passes two slightly different `Signal` objects and asserts they produce different IDs.

### 3.2. Integration Test Guidelines (High Priority)

Integration tests are essential for validating the `Dispatcher` and its interactions. These should be the primary focus of the testing effort.

*   **Goal:** Test the `Dispatcher`'s orchestration of its collaborators (`PositionManager`, `OrderManager`, etc.).
*   **Setup:**
    *   Create a test fixture that initializes a `Dispatcher` instance.
    *   Instead of heavily mocking collaborators, create "fake" versions of them (e.g., `FakePositionManager`, `FakeExchange`).
    *   These fakes should have simple, predictable, in-memory behavior. For example, `FakeExchange.execute()` should add the order to a list of `executed_orders` instead of making a network call.

#### **Priority 1: Safety Feature Tests**

1.  **Duplicate Signal Test:**
    *   **Scenario:** Send the exact same signal to `dispatcher.dispatch()` twice in a row.
    *   **Assert:** The `FakeExchange` only has **one** order in its `executed_orders` list. The second call to `dispatch()` should return a status indicating a duplicate was rejected.

2.  **Distributed Lock Test:**
    *   **Scenario:** Configure a mock `DistributedLockManager` to simulate a lock acquisition failure on the second call for the same signal.
    *   **Assert:** The `FakeExchange` only receives one order. The second signal processing attempt should be gracefully skipped.

3.  **Risk Management Test:**
    *   **Scenario:** Configure the `FakePositionManager` to have an existing position that would be violated by a new signal (e.g., exceeds max position size).
    *   **Assert:** The `FakeExchange` should have **zero** new orders. The result from `dispatch()` should indicate a risk rejection.

#### **Priority 2: OCO Manager Tests**

The `OCOManager` is complex enough to warrant its own integration test suite using a `FakeExchange`.

1.  **OCO Placement Test:**
    *   **Scenario:** Call `oco_manager.place_oco_orders()` with valid parameters.
    *   **Assert:** The `FakeExchange` should have **two** new orders in its list: one `STOP` order and one `TAKE_PROFIT` order, with the correct prices and quantities.

2.  **OCO Fill & Cancel Test (Stop-Loss):**
    *   **Scenario:**
        1.  First, place an OCO pair as above.
        2.  Simulate a "fill" by calling a method on the `FakeExchange` that marks the stop-loss order as `FILLED`.
        3.  Run the `oco_manager._monitor_orders()` task.
    *   **Assert:** The `OCOManager` should call `exchange.cancel_order()` on the take-profit order.

3.  **OCO Fill & Cancel Test (Take-Profit):**
    *   **Scenario:** Same as above, but simulate the take-profit order being filled.
    *   **Assert:** The `OCOManager` should call `exchange.cancel_order()` on the stop-loss order.

### 3.3. End-to-End (E2E) Tests

While more complex to set up, E2E tests provide the highest confidence.

*   **Goal:** Test the entire system from signal reception to order execution against a live (testnet) exchange.
*   **Scenario:**
    1.  Publish a signal to the NATS queue that the `tradeengine` is listening to.
    2.  Observe the system's behavior by querying the application's API or checking the testnet exchange directly.
    *   **Assert:** A real order was created on the testnet exchange with the correct parameters.

## 4. Recommended Next Steps

1.  **Create a `tests/integration` directory** to house the new integration tests.
2.  **Implement `Fake` collaborators** (`FakeExchange`, `FakePositionManager`) that can be used in test fixtures.
3.  **Prioritize writing the "Safety Feature Tests"** as described in section 3.2. These address the most critical risks.
4.  **Write the `OCOManager` integration tests.**
5.  Gradually build out a suite of integration tests covering all major signal actions (buy, sell, close).
