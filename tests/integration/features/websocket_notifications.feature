Feature: WebSocket Notifications

  Scenario: Client receives real-time status updates
    Given a client is connected via WebSocket
    And subscribed to transaction "tx_123"
    When the transaction moves through states
    Then the client should receive events:
      | event_type | status    | timestamp |
      | update     | PROPOSING | <time>    |
      | update     | ACCEPTED  | <time+5s> |
      | update     | FINALIZED | <time+10s>|