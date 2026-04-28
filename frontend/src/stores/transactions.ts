import { defineStore } from 'pinia';
import { ref, computed, watch } from 'vue';
import type { TransactionItem } from '@/types';
import type { TransactionHash } from 'genlayer-js/types';
import {
  isDecidedState,
  transactionsStatusNameToNumber,
} from 'genlayer-js/types';
import { useDb, useGenlayer, useWebSocketClient } from '@/hooks';
import { useNetworkStore } from '@/stores/network';

// Non-Studio chains have no WebSocket push (tx status updates come via
// `useTransactionListener` which is wired to WS events that only Studio
// emits). Poll while any tx is still undecided so CANCELED / FINALIZED /
// TIMEOUT states actually reach the UI.
const NON_STUDIO_POLL_INTERVAL_MS = 5_000;
const NON_STUDIO_NOT_FOUND_GRACE_MS = 30_000;

export const useTransactionsStore = defineStore('transactionsStore', () => {
  const genlayer = useGenlayer();
  const genlayerClient = computed(() => genlayer.client.value);
  const networkStore = useNetworkStore();
  const webSocketClient = useWebSocketClient();
  const allTransactions = ref<TransactionItem[]>([]);
  const transactions = computed<TransactionItem[]>(() =>
    allTransactions.value.filter(
      (t) => t.chainId === undefined || t.chainId === networkStore.chainId,
    ),
  );
  const subscriptions = new Set<string>();
  const missingTransactionSince = new Map<string, number>();
  const db = useDb();

  // Named handler for WebSocket reconnection
  const handleReconnection = () => {
    // Resubscribe to all transaction topics after reconnect/restart
    if (subscriptions.size > 0) {
      webSocketClient.emit('subscribe', Array.from(subscriptions));
    }
  };

  // Handle WebSocket reconnection to restore transaction subscriptions
  // Use off/on pattern to prevent duplicate listeners during HMR/re-inits
  webSocketClient.off('connect', handleReconnection);
  webSocketClient.on('connect', handleReconnection);

  // On network change, drop WS subscriptions (they are Studio-only anyway)
  // and re-subscribe to the current network's pending-ish txs on Studio.
  // On non-Studio, start the polling loop.
  watch(
    () => networkStore.chainId,
    () => {
      unsubscribeAll();
      if (networkStore.isStudio) {
        stopUndecidedPolling();
        initSubscriptions();
      } else {
        startUndecidedPolling();
      }
    },
  );

  let undecidedPollTimer: ReturnType<typeof setInterval> | null = null;

  function hasUndecidedTx() {
    return transactions.value.some(
      (tx) => !isDecidedState(transactionsStatusNameToNumber[tx.statusName]),
    );
  }

  function startUndecidedPolling() {
    if (undecidedPollTimer || !hasUndecidedTx()) return;
    undecidedPollTimer = setInterval(async () => {
      if (!hasUndecidedTx()) {
        stopUndecidedPolling();
        return;
      }
      try {
        await refreshPendingTransactions();
      } catch (err) {
        // Keep polling even if a single refresh fails.
        console.error('Failed polling transaction statuses', err);
      }
    }, NON_STUDIO_POLL_INTERVAL_MS);
  }

  function stopUndecidedPolling() {
    if (undecidedPollTimer) {
      clearInterval(undecidedPollTimer);
      undecidedPollTimer = null;
    }
  }

  function parseCreatedAt(tx: TransactionItem) {
    const createdAt = tx.data?.created_at;
    if (!createdAt) return null;

    const parsed = new Date(createdAt).getTime();
    return Number.isFinite(parsed) ? parsed : null;
  }

  function normalizeStoredTransaction(tx: TransactionItem): TransactionItem {
    return {
      ...tx,
      addedAt: tx.addedAt ?? parseCreatedAt(tx) ?? Date.now(),
    };
  }

  function normalizeRemoteTransaction(tx: any, fallbackHash?: string) {
    if (!tx) return null;

    const hash = tx.hash ?? tx.txId ?? fallbackHash;
    if (!hash) return null;

    return {
      ...tx,
      hash,
    };
  }

  function shouldRemoveMissingTransaction(tx: TransactionItem) {
    const now = Date.now();
    const firstMissAt = missingTransactionSince.get(tx.hash);

    if (firstMissAt === undefined) {
      missingTransactionSince.set(tx.hash, now);
      return false;
    }

    const addedAt = tx.addedAt ?? firstMissAt;
    return (
      now - addedAt >= NON_STUDIO_NOT_FOUND_GRACE_MS &&
      now - firstMissAt >= NON_STUDIO_POLL_INTERVAL_MS
    );
  }

  function setAllTransactions(items: TransactionItem[]) {
    allTransactions.value = items.map(normalizeStoredTransaction);
    if (!networkStore.isStudio) {
      startUndecidedPolling();
    }
  }

  function addTransaction(tx: TransactionItem) {
    const stamped = {
      ...tx,
      chainId: tx.chainId ?? networkStore.chainId,
      addedAt: tx.addedAt ?? Date.now(),
    };
    allTransactions.value.unshift(stamped); // Push on top in case there's no date property yet
    subscribe([stamped.hash]);
    if (!networkStore.isStudio) {
      startUndecidedPolling();
    }
  }

  function removeTransaction(tx: TransactionItem) {
    missingTransactionSince.delete(tx.hash);
    allTransactions.value = allTransactions.value.filter(
      (t) => t.hash !== tx.hash,
    );
    unsubscribe(tx.hash);
  }

  function updateTransaction(tx: any) {
    // SDK exposes the same id under two names — `hash` on Studio responses,
    // `txId` on testnet (Solidity struct field). Accept either.
    const normalizedTx = normalizeRemoteTransaction(tx);
    if (!normalizedTx) {
      console.warn('Transaction missing hash', tx);
      return;
    }

    const currentTxIndex = allTransactions.value.findIndex(
      (t) => t.hash === normalizedTx.hash,
    );

    if (currentTxIndex !== -1) {
      const currentTx = allTransactions.value[currentTxIndex];
      if (!currentTx) return;

      missingTransactionSince.delete(currentTx.hash);
      allTransactions.value.splice(currentTxIndex, 1, {
        ...currentTx,
        statusName: normalizedTx.statusName,
        data: normalizedTx,
      });
    } else {
      // Temporary logging to debug always-PENDING transactions
      console.warn('Transaction not found', tx);
      console.trace('updateTransaction', tx);
    }
  }

  async function getTransaction(hash: TransactionHash) {
    return genlayerClient.value?.getTransaction({ hash });
  }

  async function refreshPendingTransactions() {
    // Only refresh txs belonging to the current network — querying cross-network
    // hashes would silently "not find" them and drop them from the store.
    // Skip fully-decided txs (CANCELED / FINALIZED / *_TIMEOUT / UNDETERMINED /
    // ACCEPTED) — their status isn't going to change.
    const pendingTxs = transactions.value.filter(
      (tx: TransactionItem) =>
        !isDecidedState(transactionsStatusNameToNumber[tx.statusName]),
    ) as TransactionItem[];

    await Promise.all(
      pendingTxs.map(async (tx) => {
        const newTx = await getTransaction(tx.hash as TransactionHash);
        const normalizedTx = normalizeRemoteTransaction(newTx, tx.hash);

        if (normalizedTx) {
          updateTransaction(normalizedTx);
          await db.transactions.where('hash').equals(tx.hash).modify({
            statusName: normalizedTx.statusName,
            data: normalizedTx,
          });
        } else if (
          networkStore.isStudio ||
          shouldRemoveMissingTransaction(tx)
        ) {
          removeTransaction(tx);
          await db.transactions.where('hash').equals(tx.hash).delete();
        }
      }),
    );
  }

  async function clearTransactionsForContract(contractId: string) {
    const contractTxs = allTransactions.value.filter(
      (t) => t.localContractId === contractId,
    );

    contractTxs.forEach((t) => unsubscribe(t.hash));
    contractTxs.forEach((t) => missingTransactionSince.delete(t.hash));

    allTransactions.value = allTransactions.value.filter(
      (t) => t.localContractId !== contractId,
    );

    await db.transactions.where('localContractId').equals(contractId).delete();
  }

  async function setTransactionAppeal(tx_address: `0x${string}`) {
    await genlayerClient.value?.appealTransaction({
      txId: tx_address,
    });
  }

  async function cancelTransaction(txHash: `0x${string}`) {
    await genlayerClient.value?.cancelTransaction({
      hash: txHash as TransactionHash,
    });
  }

  function subscribe(topics: string[]) {
    topics.forEach((topic) => {
      subscriptions.add(topic);
    });
    if (webSocketClient.connected) {
      webSocketClient.emit('subscribe', topics);
    }
  }

  function unsubscribe(topic: string) {
    const deleted = subscriptions.delete(topic);
    if (deleted && webSocketClient.connected) {
      webSocketClient.emit('unsubscribe', [topic]);
    }
  }

  function unsubscribeAll() {
    const topics = Array.from(subscriptions);
    subscriptions.clear();
    if (topics.length > 0 && webSocketClient.connected) {
      webSocketClient.emit('unsubscribe', topics);
    }
  }

  function initSubscriptions() {
    // Only subscribe to the current network's txs; testnet WS is a no-op stub.
    subscribe(transactions.value.map((t) => t.hash));
  }

  async function resetStorage() {
    unsubscribeAll();
    missingTransactionSince.clear();
    allTransactions.value = [];
    await db.transactions.clear();
  }

  return {
    transactions,
    allTransactions,
    getTransaction,
    addTransaction,
    removeTransaction,
    updateTransaction,
    clearTransactionsForContract,
    setTransactionAppeal,
    cancelTransaction,
    refreshPendingTransactions,
    initSubscriptions,
    setAllTransactions,
    resetStorage,
    startUndecidedPolling,
    stopUndecidedPolling,
  };
});
