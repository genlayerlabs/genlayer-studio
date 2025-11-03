import { localnet } from 'genlayer-js/chains';
import { createClient, createAccount } from 'genlayer-js';
import type { GenLayerClient } from 'genlayer-js/types';
import { ref, watch, type Ref } from 'vue';
import { useAccountsStore } from '@/stores';

type UseGenlayerReturn = {
  client: Ref<GenLayerClient<typeof localnet> | null>;
  initClient: () => void;
};

export function useGenlayer(): UseGenlayerReturn {
  const accountsStore = useAccountsStore();
  const client = ref<GenLayerClient<typeof localnet> | null>(null);

  if (!client.value) {
    initClient();
  }

  watch([() => accountsStore.selectedAccount?.address], () => {
    initClient();
  });

  function initClient() {
    const clientAccount =
      accountsStore.selectedAccount?.type === 'local'
        ? createAccount(accountsStore.selectedAccount?.privateKey)
        : accountsStore.selectedAccount?.address;

    const endpoint =
      import.meta.env.VITE_JSON_RPC_SERVER_URL || 'http://localhost:4000/api';

    client.value = createClient({
      chain: localnet,
      endpoint,
      account: clientAccount,
    });
  }

  return {
    client,
    initClient,
  };
}
