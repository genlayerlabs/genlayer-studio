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
      subscriptions.clear();
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
    if (undecidedPollTimer) return;
    undecidedPollTimer = setInterval(async () => {
      if (!hasUndecidedTx()) return;
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

  function setAllTransactions(items: TransactionItem[]) {
    allTransactions.value = items;
  }

  function addTransaction(tx: TransactionItem) {
    const stamped = {
      ...tx,
      chainId: tx.chainId ?? networkStore.chainId,
    };
    allTransactions.value.unshift(stamped); // Push on top in case there's no date property yet
    subscribe([stamped.hash]);
  }

  function removeTransaction(tx: TransactionItem) {
    allTransactions.value = allTransactions.value.filter(
      (t) => t.hash !== tx.hash,
    );
    unsubscribe(tx.hash);
  }

  function updateTransaction(tx: any) {
    const currentTxIndex = allTransactions.value.findIndex(
      (t) => t.hash === tx.hash,
    );

    if (currentTxIndex !== -1) {
      const currentTx = allTransactions.value[currentTxIndex];
      if (!currentTx) return;

      allTransactions.value.splice(currentTxIndex, 1, {
        ...currentTx,
        statusName: tx.statusName,
        data: tx,
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

        if (newTx) {
          updateTransaction(newTx);
          await db.transactions.where('hash').equals(tx.hash).modify({
            statusName: newTx.statusName,
            data: newTx,
          });
        } else {
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

  function initSubscriptions() {
    // Only subscribe to the current network's txs; testnet WS is a no-op stub.
    subscribe(transactions.value.map((t) => t.hash));
  }

  async function resetStorage() {
    allTransactions.value.forEach((t) => unsubscribe(t.hash));
    allTransactions.value = [];
    await db.transactions.clear();
  }

  // Kick off non-Studio polling at store creation if the user lands directly
  // on a testnet (chainId watcher only fires on changes, not on initial load).
  if (!networkStore.isStudio) {
    startUndecidedPolling();
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
