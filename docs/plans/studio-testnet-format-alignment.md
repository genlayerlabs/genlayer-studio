# Studio / Testnet Transaction Format Alignment

Studio needs to be updated so its transaction response format matches the testnet (Bradbury) format. This document captures the differences.

## Transaction Response: `getTransactionData` Struct

### Field-by-field comparison

| # | Studio (localnet) | Testnet Bradbury | Notes |
|---|---|---|---|
| **Identity** | | | |
| tx hash | `hash` (string) | `txId` (bytes32) | Different field name |
| sender | `from_address` | `sender` | Different field name |
| recipient | `to_address` | `recipient` | Different field name |
| status | `status` (string enum: `"ACCEPTED"`) | `status` (uint8: `5`) | String vs numeric |
| **Timing** | | | |
| created | `created_at` (ISO string) | `createdTimestamp` (uint256, unix) | Different name and format |
| current timestamp | not returned | `currentTimestamp` (uint256) | Missing in studio |
| last vote timestamp | not returned | `lastVoteTimestamp` (uint256) | Missing in studio |
| **Consensus config** | | | |
| validators | via `sim_config` (JSON) | not in tx struct | Different mechanism |
| initial rotations | `config_rotation_rounds` | `initialRotations` (uint256) | Different name; studio maps to `initial_rotations` in studio-format response |
| num of rounds | not returned | `numOfRounds` (uint256) | Missing in studio |
| **Transaction data** | | | |
| tx data | `data` (JSON object) | split into 3 fields: | Completely different structure |
| | `data.contract_address` | `txCalldata` (bytes, RLP-encoded) | Contract address derived from calldata decode on testnet |
| | `data.contract_code` (base64) | — (embedded in txCalldata) | Code is inside the RLP payload |
| | `data.calldata` (base64) | — (embedded in txCalldata) | Calldata is inside the RLP payload |
| execution hash | not returned | `txExecutionHash` (bytes32) | Missing in studio |
| eq blocks outputs | not returned | `eqBlocksOutputs` (bytes) | Missing in studio |
| tx receipt | not returned | — (was `txReceipt` in Asimov, removed in Bradbury) | |
| **Consensus result** | | | |
| result | `consensus_data.leader_receipt[].result` (base64 string) | `result` (uint8 enum) | Studio: execution output. Testnet: consensus outcome enum |
| result name | not returned | mapped from `result` enum: IDLE/AGREE/DISAGREE/TIMEOUT/etc | Missing in studio |
| status name | `status` field is already a string | mapped from `status` enum | Studio already uses strings |
| **Round data** | | | |
| last round | via `consensus_data` (JSON) | `lastRound` (struct) | Different structure |
| | | `.round` (uint256) | |
| | | `.leaderIndex` (uint256) | |
| | | `.votesCommitted` (uint256) | |
| | | `.votesRevealed` (uint256) | |
| | | `.appealBond` (uint256) | |
| | | `.rotationsLeft` (uint256) | |
| | | `.result` (uint8) | |
| | | `.roundValidators` (address[]) | |
| | | `.validatorVotes` (uint8[]) | |
| | | `.validatorVotesHash` (bytes32[]) | |
| | | `.validatorResultHash` (bytes32[]) | Bradbury-only, not in Asimov |
| **Queue** | | | |
| queue type | derived from status in `_process_queue()` | `queueType` (uint8) | Studio derives it; testnet returns it |
| queue position | not returned | `queuePosition` (uint256) | Missing in studio |
| **Activation** | | | |
| activator | not returned | `activator` (address) | Missing in studio |
| last leader | not returned | `lastLeader` (address) | Missing in studio |
| **Block range** | | | |
| read state block range | not returned | `readStateBlockRange` (struct) | Missing in studio |
| | | `.activationBlock` (uint256) | |
| | | `.processingBlock` (uint256) | |
| | | `.proposalBlock` (uint256) | |
| **Other** | | | |
| random seed | not returned | `randomSeed` (bytes32) | Missing in studio |
| tx slot | not returned | `txSlot` (uint256) | Missing in studio |

## Messages Struct

| Field | Studio | Testnet Bradbury |
|---|---|---|
| message type | — | `messageType` (uint8) |
| recipient | `address` | `recipient` (address) |
| value | `value` | `value` (uint256) |
| data/calldata | `calldata` (base64) | `data` (bytes) |
| on acceptance | `on` (`"accepted"` / `"finalized"`) | `onAcceptance` (bool) |
| salt nonce | `salt_nonce` | `saltNonce` (uint256) |
| code | `code` (for deploys) | — (embedded in data) |

## GenLayer RPC Methods

These methods exist in studio but NOT on testnet RPC:

| Method | Studio | Testnet |
|---|---|---|
| `gen_getContractCode` | Returns base64 contract source | **Not available** — `contractActions` throws "not supported on this network" |
| `gen_getContractSchema` | Returns JSON schema | **Not available** — same |
| `gen_getContractSchemaForCode` | Returns schema for code string | **Not available** — same |
| `gen_call` | Returns bare hex string (e.g. `"818080a8..."`) | Returns object: `{"data": "818080...", "eqOutputs": [], "status": {"code": 0, "message": "success"}, "stdout": "", "stderr": "", "logs": [...]}` |
| `sim_call` | Simulates with full state | **Not available** — studio-only |
| `sim_cancelTransaction` | Cancels pending tx | **Not available** — studio-only |

## Status Enums

Studio and testnet use the same status values, but studio uses string names and testnet uses numeric codes:

| Status | Studio String | Testnet Number |
|---|---|---|
| UNINITIALIZED | — | 0 |
| PENDING | `PENDING` | 1 |
| PROPOSING | `PROPOSING` | 2 |
| COMMITTING | `COMMITTING` | 3 |
| REVEALING | `REVEALING` | 4 |
| ACCEPTED | `ACCEPTED` | 5 |
| UNDETERMINED | `UNDETERMINED` | 6 |
| FINALIZED | `FINALIZED` | 7 |
| CANCELED | `CANCELED` | 8 |

Studio also has `ACTIVATED` status which maps to `PENDING` on testnet.

## Execution Mode

| Mode | Studio Value | Testnet |
|---|---|---|
| Normal | `NORMAL` (0) | Default behavior |
| Leader only | `LEADER_ONLY` (1) | Passed in RLP-encoded tx data |
| Leader self-validator | `LEADER_SELF_VALIDATOR` (2) | — |

## Key Alignment Actions

1. **Transaction response format**: Studio should return the Bradbury struct field names and types, or at minimum provide a compatibility layer that maps between formats.
2. **Numeric enums**: Studio should return numeric status/result codes alongside string names (or switch to numeric).
3. **Transaction data split**: Studio packs everything in `data` JSON. Testnet uses `txCalldata` (RLP bytes), `txExecutionHash`, `eqBlocksOutputs` as separate fields. Studio needs to either adopt this split or ensure genlayer-js can normalize both.
4. **Missing fields**: Studio doesn't return `currentTimestamp`, `lastVoteTimestamp`, `txSlot`, `queuePosition`, `activator`, `lastLeader`, `readStateBlockRange`, `randomSeed`, `numOfRounds`. These should be added or stubbed.
5. **Messages struct**: Field names differ (`address` vs `recipient`, `calldata` vs `data`, `on` vs `onAcceptance`). Need alignment.
6. **`gen_getContractCode` / `gen_getContractSchema`**: Currently studio-only. If these are needed on testnet, they need to be added to the GenLayer RPC node, or the CLI/SDK needs alternative paths.
7. **`gen_call` response format**: Studio returns a bare hex string; testnet node returns a rich object with `data`, `status`, `eqOutputs`, `stdout`, `stderr`, `logs`. The node format is more correct since GenLayer execution produces all this metadata. **Recommended**: Studio should adopt the node's object format. `genlayer-js` currently shims both formats (`extractGenCallResult` in `contracts/actions.ts`).
