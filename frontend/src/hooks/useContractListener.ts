import { watch } from 'vue';
import { isAddress } from 'viem';
import {
  useContractsStore,
  useTransactionsStore,
  useNetworkStore,
} from '@/stores';
import { useWebSocketClient } from '@/hooks';

export function useContractListener() {
  const contractsStore = useContractsStore();
  const transactionsStore = useTransactionsStore();
  const networkStore = useNetworkStore();
  const webSocketClient = useWebSocketClient();

  function init() {
    webSocketClient.on('deployed_contract', handleContractDeployed);

    // Non-Studio chains have no `deployed_contract` WS push. Watch the local
    // transactions array — once a deploy tx lands with a non-zero recipient
    // (the consensus contract emits the new ghost address there), register
    // the contract so the user can interact with it.
    watch(
      () =>
        transactionsStore.transactions.map((t) => ({
          hash: t.hash,
          type: t.type,
          statusName: t.statusName,
          localContractId: t.localContractId,
          recipient: (t.data as any)?.recipient,
        })),
      (current) => {
        if (networkStore.isStudio) return;
        for (const tx of current) {
          if (tx.type !== 'deploy') continue;
          if (tx.statusName !== 'ACCEPTED' && tx.statusName !== 'FINALIZED')
            continue;
          if (!tx.recipient || !isAddress(tx.recipient)) continue;
          // Skip if already registered for this localContractId+address.
          const already = contractsStore.deployedContracts.find(
            (c) =>
              c.contractId === tx.localContractId && c.address === tx.recipient,
          );
          if (already) continue;
          contractsStore.addDeployedContract({
            contractId: tx.localContractId,
            address: tx.recipient,
            defaultState: '{}',
          });
        }
      },
      { deep: true, immediate: true },
    );
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
      });
    }
  }

  return {
    init,
  };
}
