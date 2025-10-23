Feature: System Performance

  Scenario: Sustained high load
    Given 100 clients are connected
    When each client submits 10 transactions per second
    And this continues for 60 seconds
    Then 95% of transactions should complete within 5 seconds
    And no transactions should be lost
    And memory usage should remain stable