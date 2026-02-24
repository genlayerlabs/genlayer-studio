Feature: Orphan Transaction Recovery
  As a consensus system
  I want to recover orphan transactions
  So that no transaction gets stuck indefinitely

  Background:
    Given the consensus system is running
    And multiple workers are available

  Scenario: Worker picks up orphan transaction in PROPOSING state
    Given a transaction "0xabc123" is in "PROPOSING" state
    And the transaction has no worker_id assigned
    And the transaction has no blocked_at timestamp
    When a consensus worker polls for available work
    Then the worker should claim the orphan transaction
    And the transaction status should change to "PENDING"
    And the worker_id should be set to the claiming worker
    And the transaction should be processed normally

  Scenario: Worker recovers transaction stuck in PROPOSING with expired timeout
    Given a transaction "0xdef456" is in "PROPOSING" state
    And the transaction has worker_id "worker-1" assigned
    And the transaction blocked_at was set 35 minutes ago
    And the transaction timeout is 30 minutes
    When a different worker "worker-2" polls for available work
    Then worker-2 should detect the expired transaction
    And worker-2 should claim the transaction
    And the transaction status should change to "PENDING"
    And the worker_id should be updated to "worker-2"

  Scenario: Multiple workers compete for orphan transaction
    Given a transaction "0x789ghi" is in "PROPOSING" state without worker_id
    When 3 workers simultaneously poll for available work
    Then exactly one worker should successfully claim the transaction
    And the other workers should not process the same transaction
    And the transaction should have only one worker_id assigned

  Scenario: Worker handles orphan transaction in COMMITTING state
    Given a transaction "0xjkl012" is in "COMMITTING" state
    And the transaction has no worker_id assigned
    And the transaction has been in this state for 5 minutes
    When a consensus worker polls for available work
    Then the worker should claim the orphan transaction
    And the worker should attempt to complete the commit process
    Or move it back to "PENDING" if commit cannot be recovered

  Scenario: Batch recovery of multiple orphan transactions
    Given the following orphan transactions exist:
      | hash       | status     | worker_id | blocked_at |
      | 0xaaa111   | PROPOSING  | null      | null       |
      | 0xbbb222   | PROPOSING  | worker-99 | 40 min ago |
      | 0xccc333   | COMMITTING | null      | null       |
    When workers poll for available work
    Then all orphan transactions should be claimed by available workers
    And each transaction should be assigned to exactly one worker
    And all transactions should progress through their lifecycle

  Scenario: Worker avoids claiming recently blocked transactions
    Given a transaction "0xmno345" is in "PROPOSING" state
    And the transaction has worker_id "worker-3" assigned
    And the transaction blocked_at was set 2 minutes ago
    And the transaction timeout is 30 minutes
    When another worker polls for available work
    Then the worker should not claim this transaction
    And the transaction should remain assigned to "worker-3"

  Scenario: System monitoring detects orphan transactions
    Given the monitoring system is active
    When orphan transactions exist for more than 5 minutes
    Then an alert should be generated
    And metrics should track orphan transaction recovery rate
    And logs should contain details of recovery attempts