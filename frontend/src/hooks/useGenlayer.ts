import { localnet } from 'genlayer-js/chains';
import { createClient, createAccount } from 'genlayer-js';
import type { GenLayerClient } from 'genlayer-js/types';
import { ref, watch, markRaw, type Ref } from 'vue';
import { useAccountsStore } from '@/stores';
import { getRuntimeConfig } from '@/utils/runtimeConfig';
import { useWallet } from './useWallet';

type UseGenlayerReturn = {
  client: Ref<GenLayerClient<typeof localnet> | null>;
  initClient: () => void;
};

export function useGenlayer(): UseGenlayerReturn {
  const accountsStore = useAccountsStore();
  const wallet = useWallet();
  const client = ref<GenLayerClient<typeof localnet> | null>(null);

  if (!client.value) {
    initClient();
  }

  watch(
    [
      () => accountsStore.selectedAccount?.address,
      () => wallet.walletProvider.value,
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
      chain: localnet,
      endpoint: getRuntimeConfig(
        'VITE_JSON_RPC_SERVER_URL',
        'http://127.0.0.1:4000/api',
      ),
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
