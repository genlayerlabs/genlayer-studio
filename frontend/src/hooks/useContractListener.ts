import { useContractsStore, useTransactionsStore } from '@/stores';
import { useWebSocketClient } from '@/hooks';

export function useContractListener() {
  const contractsStore = useContractsStore();
  const transactionsStore = useTransactionsStore();
  const webSocketClient = useWebSocketClient();

  function init() {
    webSocketClient.on('deployed_contract', handleContractDeployed);
  }

  async function handleContractDeployed(eventData: any) {
    const localDeployTx = transactionsStore.transactions.find(
      (t) => t.hash === eventData.transaction_hash,
    );

    // Check for a local transaction to:
    // - match the contract file ID since it is only stored client-side
    // - make sure to scope the websocket event to the correct client
    if (localDeployTx) {
      contractsStore.addDeployedContract({
        contractId: localDeployTx.localContractId,
        address: eventData.data.id,
        defaultState: eventData.data.data.state,
        deployTxHash: localDeployTx.hash,
      });
    }
  }

  return {
    init,
  };
}
