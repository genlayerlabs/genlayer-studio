import { localnet, studionet, testnetAsimov } from 'genlayer-js/chains';
import { createClient, createAccount } from 'genlayer-js';
import type { GenLayerClient } from 'genlayer-js/types';
import { ref, watch, markRaw, type Ref } from 'vue';
import { useAccountsStore } from '@/stores';
import { getRuntimeConfig } from '@/utils/runtimeConfig';
import { useWallet } from './useWallet';

const chains: Record<string, typeof localnet> = {
  localnet,
  studionet,
  testnetAsimov,
};

function getChain() {
  const networkName = getRuntimeConfig('VITE_GENLAYER_NETWORK', 'localnet');
  const chain = chains[networkName];
  if (!chain) {
    throw new Error(
      `Unknown VITE_GENLAYER_NETWORK: "${networkName}". Must be one of: ${Object.keys(chains).join(', ')}`,
    );
  }

  return chain;
}

type UseGenlayerReturn = {
  client: Ref<GenLayerClient<any> | null>;
  initClient: () => void;
};

export function useGenlayer(): UseGenlayerReturn {
  const accountsStore = useAccountsStore();
  const wallet = useWallet();
  const client = ref<GenLayerClient<any> | null>(null);

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
      chain: getChain(),
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
