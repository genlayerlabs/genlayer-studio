import { createClient, createAccount } from 'genlayer-js';
import type { GenLayerClient } from 'genlayer-js/types';
import { ref, watch, markRaw, type Ref } from 'vue';
import { useAccountsStore } from '@/stores';
import { useNetworkStore } from '@/stores/network';
import { useWallet } from './useWallet';

type UseGenlayerReturn = {
  client: Ref<GenLayerClient<any> | null>;
  initClient: () => void;
};

export function useGenlayer(): UseGenlayerReturn {
  const accountsStore = useAccountsStore();
  const networkStore = useNetworkStore();
  const wallet = useWallet();
  const client = ref<GenLayerClient<any> | null>(null);

  if (!client.value) {
    initClient();
  }

  watch(
    [
      () => accountsStore.selectedAccount?.address,
      () => wallet.walletProvider.value,
      () => networkStore.currentNetwork,
      () => networkStore.rpcUrl,
    ],
    () => {
      initClient();
    },
  );

  function initClient() {
    const clientAccount =
      accountsStore.selectedAccount?.type === 'local'
        ? createAccount(accountsStore.selectedAccount?.privateKey)
        : accountsStore.selectedAccount?.address;

    const clientOptions: Record<string, unknown> = {
      chain: networkStore.chain,
      endpoint: networkStore.rpcUrl,
      account: clientAccount,
    };

    // Pass EIP-1193 provider for external wallets so genlayer-js can request signatures
    if (
      accountsStore.selectedAccount?.type === 'external' &&
      wallet.walletProvider.value
    ) {
      clientOptions.provider = wallet.walletProvider.value;
    }

    client.value = markRaw(createClient(clientOptions as any));
  }

  return {
    client,
    initClient,
  };
}
