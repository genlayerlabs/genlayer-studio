@parallel @cleanup_transactions
Feature: Parallel Contract Deployment
  
  Background:
    Given all required containers are running
    
  Scenario: Deploy multiple contracts simultaneously
    Given I have a slow contract that takes time to initialize
    When I start deploying the contract 5 times in parallel
    Then within 2 seconds all 5 transactions should have status "PROPOSING"
    And all 5 transactions should have different transaction IDs