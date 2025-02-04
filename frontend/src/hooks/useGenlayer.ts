import { simulator } from 'genlayer-js/chains';
import { createClient, createAccount } from 'genlayer-js';
import type { GenLayerClient } from 'genlayer-js/types';
import { ref, watch, type Ref } from 'vue';
import { useAccountsStore } from '@/stores';

type UseGenlayerReturn = {
  client: Ref<GenLayerClient<typeof simulator> | null>;
  initClient: () => void;
};

export function useGenlayer(): UseGenlayerReturn {
  const accountsStore = useAccountsStore();
  const client = ref<GenLayerClient<typeof simulator> | null>(null);

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

    client.value = createClient({
      chain: simulator,
      endpoint: import.meta.env.VITE_JSON_RPC_SERVER_URL,
      account: clientAccount,
    });
  }

  return {
    client,
    initClient,
  };
}
