import { computed } from 'vue';
import { useAppKitNetwork } from '@reown/appkit/vue';
import { useAccountsStore } from '@/stores';
import { createGenlayerLocalnet } from './useNetworks';
import { appKitReady } from './useAppKit';

const genlayerLocalnet = appKitReady ? createGenlayerLocalnet() : null;

/**
 * Ensures the connected external wallet is on the GenLayer network
 * before sending a transaction. For local accounts this is a no-op.
 *
 * Follows Rally2's pattern: switch, verify, throw if rejected.
 */
export function useChainEnforcer() {
  if (!appKitReady || !genlayerLocalnet) {
    return { ensureCorrectChain: async () => {} };
  }

  const networkData = useAppKitNetwork();
  const accountsStore = useAccountsStore();

  const isExternalWallet = computed(
    () => accountsStore.selectedAccount?.type === 'external',
  );

  async function ensureCorrectChain() {
    if (!isExternalWallet.value) return;
    if (networkData.value.chainId === genlayerLocalnet!.id) return;

    networkData.value.switchNetwork(genlayerLocalnet!);

    // Give the wallet a moment to process the switch
    await new Promise((r) => setTimeout(r, 500));

    if (networkData.value.chainId !== genlayerLocalnet!.id) {
      throw new Error(
        'Please switch your wallet to the GenLayer network to send transactions.',
      );
    }
  }

  return { ensureCorrectChain };
}
