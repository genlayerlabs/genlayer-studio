# Studio vs. GenLayer consensus specification

Reference: the protocol specification in
[genlayer-consensus `docs/spec`](https://github.com/genlayerlabs/genlayer-consensus/tree/main/docs/spec)
(added in genlayer-consensus#208; see Appendix F "Status, Queue and Decision
Transitions" and the Glossary).

## Scope of this page

This page is a **status inventory and difference list** for DXP-430. It does
not rename anything. Renaming stored statuses or RPC fields needs a database
enum migration and a JSON-RPC/SDK compatibility decision, so that is left to
follow-up work (see "Open differences"). Ticket-owner confirmation that this
inventory is the intended first deliverable is requested in the pull request.

## What Studio simulates

Studio simulates the consensus protocol for a configurable validator set inside
one environment (one database, one worker pool). It does model parts of the
protocol semantics:

- per-contract queue ordering: transactions are claimed and finalized in
  `queue_order` per contract (`backend/consensus/worker.py`);
- an active message generation tracked in transaction fee accounting
  (`backend/protocol_rpc/fees.py`);
- fee settlement, run once per transaction (`backend/consensus/base.py`).

It differs from the protocol in that there is no on-chain contract suite, no
staking, epochs, tribunals or economic slashing, and validators are provided by
local configuration rather than selected from a registry. Queue, generation and
fee semantics are Studio's own implementations, not a line-for-line port, and
are not guaranteed to match every spec edge.

## Transaction status

Studio keeps its stored status values (`TransactionStatus` in
`backend/database_handler/models.py`) because they are persisted in the
database enum and exposed over JSON-RPC. They map to the spec as follows.

| Studio status | Spec status | Note |
| --- | --- | --- |
| (none; reported as `UNINITIALIZED`) | `Uninitialized` | Not stored: a transaction has no row before it exists. RPC status lookups default to `UNINITIALIZED` (code 0, `TRANSACTION_STATUS_CODES`). |
| `PENDING` | `Pending` | Same. |
| `ACTIVATED` | (none) | Studio-only: picked up by the worker. Reported with the spec's `Proposing` code. |
| `CANCELED` | `Canceled` | Same. |
| `PROPOSING` | `Proposing` | Same. |
| `COMMITTING` | `Committing` | Same. |
| (none) | `LeaderRevealing` | Not modelled: leader and validators reveal together. |
| `REVEALING` | `Revealing` | Same name; covers the whole reveal step. |
| `ACCEPTED` | `Accepted` | Same. |
| `UNDETERMINED` | `Undetermined` | Same. |
| `LEADER_TIMEOUT` | `LeaderTimeout` | Same. |
| `VALIDATORS_TIMEOUT` | `ValidatorsTimeout` | Same. |
| (none; derived) | `ReadyToFinalize` | Not stored. The spec moves a decided transaction here when its appeal window expires. In Studio it is implicit: a decided transaction stays in its decided status until `can_finalize_transaction` (`backend/consensus/base.py`) sees the stored appeal deadline has passed (or the execution mode finalizes immediately) and it is at the head of its contract queue, then it moves straight to `FINALIZED`. |
| `FINALIZED` | `Finalized` | Same. |
| (none) | `AppealCommitting`, `AppealRevealing` | Studio records appeals as flags (`appealed`, `appeal_undetermined`, `appeal_leader_timeout`, `appeal_validators_timeout`) and `ConsensusRound` history entries instead of statuses. |

Decided family (spec glossary): `ACCEPTED`, `UNDETERMINED`, `LEADER_TIMEOUT`,
`VALIDATORS_TIMEOUT`.

## Open differences (not changed here)

- Renaming or adding stored statuses (`LeaderRevealing`, `ReadyToFinalize`,
  `AppealCommitting`, `AppealRevealing`, dropping `ACTIVATED`) needs a database
  enum migration and compatibility coverage.
- `ConsensusRound` values (`Leader Rotation Appeal`, `Validator Appeal
  Successful`, ...) are Studio-specific history labels with no spec equivalent.
- The spec's "Attempt" concept and its separate vote/recorder generations have
  no direct Studio counterpart.
