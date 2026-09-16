# v0.6 consumer migration

This first consumer update targets `v0.123-dev`, the active integration line
identified by release gate #1670. The older fee PR #1653 targets `v0.122` and
is not the base of this change. No open PR inspected on 16 September 2026
changes `frontend/src/stores/transactions.ts`.

Both frontend and explorer now pin SDK #220 at
`e19f45310a83048ce95ca23013894bf7375492b8`, with regenerated npm lockfiles.
That SDK was audited against Consensus #1634 at
`c4749a42095bcfb9de69bef4f4fd5e9a6a2f86f2` (above #1628). This is an exact
pre-release build, not a tagged npm release or production compatibility claim.
The frontend previously selected v2 commit `bc1fc320`; the explorer selected
v0.20.3. No staking `ValidatorInfo.live` / `ValidatorView.live` consumers were
found in either application's sources.

The frontend supports contract-backed networks as well as Studio. Its
non-Studio polling used to stop at Accepted, Undetermined and timeout
outcomes, missing later appeals/finalization. It now follows those decisions
until the stored parent status is Finalized or Canceled. Six regression cases
cover the four appealable decisions and the two terminal parent statuses.
The existing network-scoped refresh, canonical `txId`/`hash` normalization,
and whole transaction-data replacement remain in place.

The explorer's `/api/rpc` client remains on the simulator backend. Updating
its SDK does not turn that backend into the Node/Consensus stack. Likewise,
parent Finalized does not certify that all child messages or refund/payout
obligations are complete.

## Next implementation and qualification

The [cross-repository plan](https://github.com/genlayerlabs/genlayer-dev-env/blob/fix/consume-inflation-and-votewindow-parity/docs/integration/jm-claus-kiril-consumer-plan-2026-09-16.md)
records PR ownership, inspected heads, exclusions and the remaining work:

- Synchronize the implemented Node stack before selecting final runtime pins.
- Use authoritative `executionGeneration` for attempt-specific receipt-cache
  invalidation. SDK #220 exposes it; older simulator responses may omit it.
  Do not infer generation from a retained decision.
- Establish authoritative child enumeration and delivery-completion APIs;
  the SDK's existing parent-decision receipt scan does not cover deferred
  terminal delivery. Do not introduce a guessed completion indicator.
- Qualify deploy/write/read, details, appeal/recompute, nested child delivery
  and any affected staking/governance journey against the actual backend.
- Freeze the exact Node-led dependency closure and inspect the resolved E2E
  manifest. The earlier SDK-only green E2E does not qualify this combination.

Local frontend type/unit/build and explorer fee/build checks are necessary
but do not establish those runtime journeys. No backend migration, governance
UI, E2E trigger, merge, deployment or release is included here. Rollback should
revert the SDK pins together with both lockfiles; the polling correction can
be reviewed independently.
