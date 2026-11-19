import { useTransactionsStore } from '@/stores';
import type { TransactionHash } from 'genlayer-js/types';
import { useWebSocketClient } from '@/hooks';

export function useTransactionListener() {
  const transactionsStore = useTransactionsStore();
  const webSocketClient = useWebSocketClient();
  const latestRefresh = new Map<TransactionHash, symbol>();

  function init() {
    webSocketClient.on('transaction_status_updated', handleTransactionUpdate);
    webSocketClient.on('transaction_appeal_updated', handleTransactionUpdate);
  }

  async function handleTransactionUpdate(eventData: {
    data: { hash: TransactionHash };
  }) {
    const { hash } = eventData.data;
    const refresh = Symbol();
    latestRefresh.set(hash, refresh);

    try {
      const newTx = await transactionsStore.getTransaction(hash);
      // Rapid consensus transitions can return receipts out of request order.
      if (latestRefresh.get(hash) !== refresh) return;

      const currentTx = transactionsStore.transactions.find(
        (t) => t.hash === hash,
      );
      if (!currentTx) return;

      if (!newTx) {
        console.log('Server tx not found for local tx:', currentTx);
        transactionsStore.removeTransaction(currentTx);
        return;
      }

      transactionsStore.updateTransaction(newTx);
    } finally {
      if (latestRefresh.get(hash) === refresh) latestRefresh.delete(hash);
    }
  }

  return {
    init,
  };
}
