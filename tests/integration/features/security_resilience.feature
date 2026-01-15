Feature: Security Resilience
  Scenario: DOS attack mitigation
    Given an attacker sends 10000 requests per second
    When rate limiting kicks in after 100 requests
    Then subsequent requests should be throttled
    And legitimate users should still be served