# Studio vs. GenLayer consensus specification

Reference: the protocol specification in
[genlayer-consensus `docs/spec`](https://github.com/genlayerlabs/genlayer-consensus/tree/main/docs/spec)
(added in genlayer-consensus#208; see Appendix F "Status, Queue and Decision
Transitions" and the Glossary). This page records where Studio naming and
structure match the spec, where they intentionally differ, and what is left to
align. Studio is a single-node simulator, so it does not model staking,
tribunals, queues, generations or fee settlement.

## Transaction status

Studio keeps its stored status values (`TransactionStatus` in
`backend/database_handler/models.py`) because they are persisted in the
database enum and exposed over JSON-RPC. They map to the spec as follows.

| Studio status        | Spec status        | Note                                                          |
| -------------------- | ------------------ | ------------------------------------------------------------- |
| `PENDING`            | `Pending`          | Same.                                                         |
| `ACTIVATED`          | (none)             | Studio-only: picked up by the worker, spec activates straight to `Proposing`. |
| `CANCELED`           | `Canceled`         | Same.                                                         |
| `PROPOSING`          | `Proposing`        | Same.                                                         |
| `COMMITTING`         | `Committing`       | Same.                                                         |
| (none)               | `LeaderRevealing`  | Not modelled: Studio leader and validators reveal together.   |
| `REVEALING`          | `Revealing`        | Same name; in Studio it covers the whole reveal step.         |
| `ACCEPTED`           | `Accepted`         | Same.                                                         |
| `UNDETERMINED`       | `Undetermined`     | Same.                                                         |
| `LEADER_TIMEOUT`     | `LeaderTimeout`    | Same.                                                         |
| `VALIDATORS_TIMEOUT` | `ValidatorsTimeout`| Same.                                                         |
| `FINALIZED`          | `Finalized`        | Same.                                                         |
| (none)               | `AppealCommitting`, `AppealRevealing` | Studio records appeals as flags (`appealed`, `appeal_undetermined`, `appeal_leader_timeout`, `appeal_validators_timeout`) and `ConsensusRound` history entries instead of statuses. |

Decided family (spec glossary): `ACCEPTED`, `UNDETERMINED`, `LEADER_TIMEOUT`,
`VALIDATORS_TIMEOUT`.

## Open differences (not changed here)

- Renaming or adding stored statuses (`LeaderRevealing`, `AppealCommitting`,
  `AppealRevealing`, dropping `ACTIVATED`) needs a database enum migration and
  a JSON-RPC / SDK compatibility decision; it is deliberately out of scope for
  this page.
- `ConsensusRound` values (`Leader Rotation Appeal`, `Validator Appeal
  Successful`, ...) are Studio-specific history labels with no spec equivalent.
- The spec's "Decision", "Attempt" and "generation" concepts have no Studio
  counterpart.
