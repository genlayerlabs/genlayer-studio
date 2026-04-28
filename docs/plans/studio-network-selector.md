# Studio Network Selector — Full Scope Plan

**Goal:** Users of the hosted Studio frontend at `studio.genlayer.com` can pick between **Local Studio backend** and **Bradbury testnet** via a UI dropdown. (Asimov shares chain ID 4221 with Bradbury — same underlying network, different RPC frontend — so it is not a separate dropdown option. Reachable via `VITE_GENLAYER_NETWORK=testnetAsimov` deployment override only.) Contract deploy / read / write / simulate / attach flows work on all three. Studio-exclusive features (node logs, validators, providers, faucet, finality tuning) degrade gracefully on testnet. MetaMask is forced to the selected chain; burner wallets work on any chain.

---

## 1. What already exists

The infrastructure is ~80% wired at build time; only the runtime layer and UX are missing.

| Piece | File | What it does today |
|---|---|---|
| Chain selection | `frontend/src/hooks/useGenlayer.ts:19-32` | Reads `VITE_GENLAYER_NETWORK` → picks chain from `{localnet, studionet, testnetAsimov}` map |
| Chain override | `frontend/src/hooks/useGenlayer.ts:29-32` | `VITE_CHAIN_ID` overrides SDK chain.id |
| MetaMask switch/add | `frontend/src/hooks/useChainEnforcer.ts` | Fires `wallet_switchEthereumChain`, falls back to `wallet_addEthereumChain` on 4902 |
| Runtime config overlay | `frontend/src/utils/runtimeConfig.ts` | `window.__RUNTIME_CONFIG__` can inject values at container start |
| Feature flags | `frontend/src/hooks/useConfig.ts` | Already has `canUpdateValidators`, `canUpdateProviders` gated by `VITE_IS_HOSTED` |
| SDK chain defs | `genlayer-js` package | Exports `localnet`, `studionet`, `testnetAsimov` (and `testnetBradbury`) with `isStudio` flag |
| Field transform | `backend/.../transactions_processor.get_studio_transaction_by_hash` | Partial Studio→testnet field rename |

**Gap:** everything is resolved once at module load. There is no reactive "current network" anywhere in the frontend.

---

## 2. End-state UX

1. A **network selector** in the header, styled like a chip/dropdown: `● Studio` / `● Bradbury` / `● Asimov`. Active dot tinted per network.
2. Selecting a new network:
   - Persists choice to `localStorage`.
   - Re-initializes the genlayer-js client against the new chain/RPC.
   - Reconnects the WebSocket transport (close old, open new — only the Studio variant emits events).
   - If an external wallet is connected: immediately prompts MetaMask to switch chain. On rejection, revert the dropdown + toast "Wallet still on X".
   - Filters contracts/transactions UI to only the new network's records.
3. Studio-exclusive UI hides or disables on testnet (node logs, validators, providers, faucet, finality editor).
4. On reload, the last-selected network is restored. Operator's `VITE_GENLAYER_NETWORK` env var is the **default** (used only when no persisted choice); set `VITE_LOCK_NETWORK=true` to hide the dropdown entirely for single-network deployments.

---

## 3. Architecture changes

### 3.1 New Pinia store — `networkStore`

`frontend/src/stores/network.ts` (new)

```ts
state: {
  currentNetwork: 'localnet' | 'studionet' | 'testnetAsimov' | 'testnetBradbury'
}
getters: {
  chain,              // resolved GenLayerChain from genlayer-js
  rpcUrl,             // derived from chain or per-network override map
  wsUrl,              // Studio-only; null for testnets
  chainId,            // number
  isStudio,           // chain.isStudio
  isLocked,           // VITE_LOCK_NETWORK
  availableNetworks,  // filtered list for dropdown
}
actions: {
  setCurrentNetwork(name) // persists + triggers downstream re-inits
}
```

Seeds from `localStorage['networkStore.currentNetwork']` → falls back to `VITE_GENLAYER_NETWORK`.

### 3.2 Reactive transport

**Problem:** `rpc.ts` reads the URL once at module load; WebSocket singleton is constructed once; genlayer-js client is re-initialized only on account change.

**Fix:**
- `frontend/src/clients/rpc.ts` — replace the module-level `JSON_RPC_SERVER_URL` constant with a getter that reads `networkStore.rpcUrl`. Each `fetch` call resolves the URL fresh.
- `frontend/src/hooks/useWebSocketClient.ts` — add `reconnect(newUrl)` that closes the current socket, clears subscriptions, and opens a new one. Watch `networkStore.wsUrl` and call `reconnect()` on change. When `wsUrl` is null (testnet), tear down without reopening.
- `frontend/src/hooks/useGenlayer.ts` — add `watch(() => networkStore.currentNetwork, initClient)` alongside the existing account watcher. Client re-instantiation already handles `publicClient` + `walletClient` cleanly.

### 3.3 AppKit chain registration

`frontend/src/hooks/useAppKit.ts:44-48` today hardcodes `[genlayerLocalnet, mainnet, sepolia]`. This means MetaMask has no idea what Bradbury/Asimov are when switching.

**Fix:** build the AppKit networks array from the `networkStore.availableNetworks` (or from genlayer-js chain definitions). Pass `allowUnsupportedChain: true` so mid-session switches don't break AppKit's state.

### 3.4 Network selector component

`frontend/src/components/Global/NetworkSelector.vue` (new) — dropdown with the three networks, current selection highlighted, confirmation dialog if there are pending transactions on the current network (those will be filtered out of the UI after switch).

Mount in `frontend/src/components/Header.vue:13-25` between the logo and `AccountSelect`. Hide when `networkStore.isLocked`.

### 3.5 Per-network data scoping

**Accounts:** stay global. Same private key is valid on every chain; that's the standard wallet model. Balance display must re-fetch on network change (currently there's no balance widget anyway — if we add one, it reads from the reactive RPC).

**Deployed contracts** (`contractsStore.deployedContracts`, stored in IndexedDB via `frontend/src/hooks/useDb.ts`): today no `chainId` field. Add one. Without scoping, deploying `Storage` to localnet then attaching the same contract at a Bradbury address would collide.

**Transactions** (`transactionsStore.transactions`, also IndexedDB): same problem, plus worse — `refreshPendingTransactions()` (`frontend/src/stores/transactions.ts:64-85`) will query the wrong network and silently drop pending txs as "not found."

**Schema migration (Dexie v5):**
```ts
deployedContracts: '++id, contractId, [chainId+address]'
transactions:      '++id, type, statusName, chainId, [chainId+hash], [chainId+contractAddress], [chainId+localContractId]'
```
Backfill existing rows with the current `VITE_GENLAYER_NETWORK`'s chain ID on upgrade. Getters (`deployedContracts`, `transactions`) filter by `networkStore.chainId` before returning.

### 3.6 Graceful degradation

Extend `useConfig` with a new computed: `isStudioNetwork = networkStore.isStudio`.

| Component | File | On testnet |
|---|---|---|
| Node Logs pane | `frontend/src/components/Simulator/NodeLogs.vue` | Hide. Show placeholder "Live node logs are only available on local Studio." |
| Validators view | `frontend/src/views/Simulator/ValidatorsView.vue` | Already gated by `canUpdateValidators`; tighten to also require `isStudioNetwork` |
| Providers section | `frontend/src/components/Simulator/settings/ProviderSection.vue` | Same |
| Finality window editor | `frontend/src/components/Simulator/settings/ConsensusSection.vue` | Disable edit; read-only displayed value from chain config |
| Faucet button | `frontend/src/components/Simulator/AccountSelect.vue:170-207` | On testnet, swap action: open `https://testnet-faucet.genlayer.foundation` in new tab instead of calling `sim_fundAccount`. Hide amount input (faucet fixed at 100 GEN / 24h). |
| Tutorial fake-log injection | `frontend/src/stores/tutorial.ts` | Skip `nodeStore.addLog` calls; deploy step still works via genlayer-js |
| Reset storage | `frontend/src/components/Simulator/settings/SimulatorSection.vue:45` | Scope label: "Clears data for *[current network]*" — only clears rows matching chainId |
| `sim_cancelTransaction` button (if exposed) | — | Hide |
| `sim_upgradeContractCode` button (if exposed) | — | Hide |

The WebSocket itself should be torn down on non-Studio networks — no events to consume, no reason to hold the connection.

### 3.7 Wallet / chain enforcement on switch

Today `ensureCorrectChain()` (`useChainEnforcer.ts`) fires **only before deploy/write**. That leaves a window where the UI says "Bradbury" but MetaMask is still on localnet until the first transaction.

**Fix:** on `networkStore.setCurrentNetwork`, if `accountsStore.currentAccount.type === 'external'`, call `ensureCorrectChain()` immediately. On user rejection (error 4001): revert `networkStore.currentNetwork` to the previous value and toast "Keep wallet on previous network — switch canceled."

Also handle: user changes MetaMask's network manually → `eth_chainChanged` event → sync `networkStore` to match (if the chain is one we know) or show a banner "Wallet is on an unknown chain."

---

## 4. Verification before / during implementation

These are the known unknowns. Settle them early — they affect scope:

1. **Does Bradbury/Asimov support `gen_call`, `gen_getContractCode`, `gen_getContractSchema`, `gen_getContractNonce`?**
   - **Resolved.** All three supported; `gen_getContractNonce` isn't a real RPC on any chain (nonces come from `eth_getTransactionCount`).
   - `gen_getContractSchema` has **divergent semantics** between Studio (takes `address`) and the node (takes base64 `code`). See `docs/plans/studio-testnet-format-alignment.md` § "Schema method divergence".
   - **Unblocker for this plan:** patch `genlayer-js` to hide the divergence — `getContractSchema(address)` does a `gen_getContractCode` then `gen_getContractSchema({code})` on non-Studio chains, single RPC on Studio. Also remove the `if (!isStudio) throw` guards on `getContractCode` / `getContractSchema` / `getContractSchemaForCode`. Once the SDK patch ships, contract attach and deployed-contract method UI work unchanged on testnet.
   - **Long term:** align the two sides at the protocol level and drop the SDK workaround. Tracked in the divergence doc.

2. **Does genlayer-js `extractGenCallResult` shim actually handle both Studio and Bradbury response shapes?**
   - User believes yes. Verify by grep'ing genlayer-js source and spot-checking on a Bradbury RPC.

3. **Chain IDs for Bradbury / Asimov** — **Resolved.** Both share chain ID `4221` by design: they are different RPC frontends onto the same underlying chain. Distinct consensus/staking contract addresses per chain def, but same network from MetaMask's perspective. **v1 ships Bradbury only.** Asimov reachable via deployment-time `VITE_GENLAYER_NETWORK=testnetAsimov` override if ever needed, but not in the runtime dropdown — the shared chain ID makes simultaneous MetaMask support confusing (can't distinguish which RPC MetaMask's "4221" points at).

4. **Public faucet URLs** — **Resolved.** `https://testnet-faucet.genlayer.foundation` (100 GEN per 24h, gated on 0.01 ETH mainnet balance). Shared between Bradbury and Asimov. On Bradbury, faucet button opens this URL in a new tab; amount input is hidden. Worth noting in the network-switch warning banner that burner-only wallets can't self-fund (faucet gates on mainnet balance).

5. **Does `sim_lintContract` have a testnet equivalent?** — **Resolved.** No. Decision: linting is a developer tool, not a chain operation — keep the Monaco linter pointed at the Studio backend regardless of the currently selected target network. In hosted deployments that have no Studio backend at all, the linter disables cleanly (no inline errors; editor still usable). Means `monacoLinter.ts` keeps using `VITE_JSON_RPC_SERVER_URL` directly rather than routing through `networkStore.rpcUrl`.

6. **WebSocket on testnet** — **Resolved.** Bradbury has no WebSocket / push-event support, so drop the WS entirely when the selected network is non-Studio. Tear down the singleton on switch to a testnet, suppress its auto-reconnect behavior; rebuild cleanly on switch back to Studio. Transaction status falls back to polling `eth_getTransactionByHash` (the `refreshPendingTransactions` path at `frontend/src/stores/transactions.ts:64-85` is already the fallback).

---

## 5. Implementation phases

### Phase 0 — Verification & scaffolding (pre-code)
- Confirm the 6 open questions above. File a followup for any that require SDK or backend work before the UI can ship.
- Snapshot the current `refactor/multi-network-prep` branch; decide whether to rebase on `main` and continue, or start fresh on a new branch. (Recommend fresh; the existing branch is 27 behind and its one commit already landed on main.)

### Phase 1 — Reactive transport (no UI yet)
- Add `networkStore` (seeded from env, no dropdown yet).
- Make `rpc.ts` URL reactive.
- Make WebSocket singleton reconnect on URL change / tear down when URL is null.
- Add network watcher to `useGenlayer`.
- Test: set `networkStore.currentNetwork` from devtools, verify next RPC call hits the new URL.

### Phase 2 — Data scoping
- Dexie v5 migration: add `chainId` to `deployedContracts` and `transactions`.
- Backfill on upgrade.
- Update store getters / computed to filter by `networkStore.chainId`.
- Update `contractsStore.addDeployedContract`, `transactionsStore.addTransaction` to stamp `chainId`.
- Update `importContract` duplicate check to include `chainId`.

### Phase 3 — UI selector + wallet flow
- `NetworkSelector.vue` in header.
- Persistence to localStorage.
- On selection change: trigger `ensureCorrectChain()` when external wallet is connected; revert on rejection.
- Register all supported chains in AppKit.
- Listen for MetaMask `chainChanged` and sync store.

### Phase 4 — Degradation
- Extend `useConfig` with `isStudioNetwork`.
- Gate Node Logs, Validators, Providers, Finality editor, `sim_*` buttons.
- Swap faucet button behavior for testnet.
- Skip tutorial log injection on testnet.
- Scope reset-storage button label + effect to current network.

### Phase 5 — Polish + test
- Banner on testnet: "You are on Bradbury testnet. Contracts and transactions deployed here are separate from your local Studio work."
- E2E tests per network: deploy, write, read, simulate, attach. Separate spec file per network, parameterized.
- Manual test matrix: burner-only, MetaMask on localnet, MetaMask on wrong chain, switch mid-pending-tx, reload preserves selection.
- Sentry events tagged with current network.

---

## 6. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| `gen_getContractCode` / `gen_getContractSchema` don't exist on testnet → contract attach breaks | Medium | Fall back to `eth_getCode` + client-side ABI parse, or disable attach UI on testnet with explanatory message |
| `gen_call` response shape differs → simulate returns garbage | Medium | Verify genlayer-js shim; if missing, add response normalization in `useContractQueries.simulateWriteMethod` |
| Pending txs silently lost on network switch | High (if we don't scope) | Phase 2 (data scoping) is a hard prerequisite for the dropdown ship |
| MetaMask refuses to add Bradbury (e.g. bad RPC URL) | Medium | Test `wallet_addEthereumChain` payload against each network manually; document fallback "add it yourself" |
| AppKit holds stale chain state after switch | Medium | Use `allowUnsupportedChain`; reinit AppKit network list on selection change |
| Existing IndexedDB users break on schema v5 | Low | Dexie upgrade hook backfills `chainId = localnet`; existing data stays visible on localnet |
| Hosted operator wants to disable the dropdown | Low | `VITE_LOCK_NETWORK=true` hides selector and forces `VITE_GENLAYER_NETWORK` |

---

## 7. Out of scope

- Format-alignment work (that's `docs/plans/studio-testnet-format-alignment.md`). Plan assumes genlayer-js handles response-shape divergence. If it doesn't, that's a separate branch.
- Per-network account scoping (accounts stay global — standard wallet behavior).
- Sharing or importing a contract URL that encodes its network (`?network=bradbury&address=0x...`). Nice-to-have, defer.
- Testnet block explorer deep-links from tx rows. Nice-to-have, defer.
- Switching Studio's consensus contracts / staking contracts per network — those come from genlayer-js chain defs, already handled.

---

## 8. File change summary

**New:**
- `frontend/src/stores/network.ts`
- `frontend/src/components/Global/NetworkSelector.vue`

**Modified:**
- `frontend/src/clients/rpc.ts` — reactive URL
- `frontend/src/hooks/useWebSocketClient.ts` — reconnect API
- `frontend/src/hooks/useGenlayer.ts` — watch network
- `frontend/src/hooks/useChainEnforcer.ts` — triggerable from store action
- `frontend/src/hooks/useAppKit.ts` — dynamic chain list
- `frontend/src/hooks/useConfig.ts` — add `isStudioNetwork`
- `frontend/src/hooks/useDb.ts` — Dexie v5 migration
- `frontend/src/stores/contracts.ts` — chainId stamping, scoped getter
- `frontend/src/stores/transactions.ts` — chainId stamping, scoped getter, scoped polling
- `frontend/src/stores/tutorial.ts` — skip log injection on testnet
- `frontend/src/components/Header.vue` — mount selector
- `frontend/src/components/Simulator/AccountSelect.vue` — faucet swap
- `frontend/src/components/Simulator/NodeLogs.vue` — hide on testnet
- `frontend/src/components/Simulator/settings/ConsensusSection.vue` — read-only on testnet
- `frontend/src/components/Simulator/settings/SimulatorSection.vue` — scoped reset label
- `frontend/src/composables/useContractImport.ts` — scoped duplicate check
- `.env.example` — add `VITE_LOCK_NETWORK`

**Tests:**
- `frontend/test/unit/stores/network.test.ts` (new)
- `frontend/test/unit/hooks/useGenlayer.test.ts` — watcher behavior
- `frontend/test/unit/hooks/useDb.test.ts` — migration
- `frontend/test/e2e/` — parameterized specs per network

---

## 9. Rough estimate

2–3 weeks of focused frontend work, assuming the Phase 0 verifications don't uncover SDK gaps:
- Phase 1 (reactive transport): 2–3 days
- Phase 2 (data scoping + migration): 3–4 days
- Phase 3 (UI + wallet flow): 3–4 days
- Phase 4 (degradation): 2–3 days
- Phase 5 (polish + test): 2–3 days

If SDK-level work is needed for testnet `gen_*` methods or response-shape normalization, add another 1–2 weeks.
