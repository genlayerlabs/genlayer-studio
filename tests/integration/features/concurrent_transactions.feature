Feature: Concurrent Transactions
  
  Scenario: Multiple transactions for same contract address
    Given I have a contract at address "0x123"
    When I submit 3 different updates simultaneously
    Then only 1 transaction should be accepted
    And the other 2 should be queued as "PENDING"
    
  Scenario: Rapid fire transactions from same user
    Given a user has a rate limit of 10 tx/minute
    When the user submits 15 transactions in 10 seconds
    Then the first 10 should be processed
    And the remaining 5 should be queued or rejected