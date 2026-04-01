import { computed } from 'vue';
import { numberToHex } from 'viem';
import { useAccountsStore } from '@/stores';
import { useWallet } from './useWallet';
import { useGenlayer } from './useGenlayer';
import { getRuntimeConfig } from '@/utils/runtimeConfig';

/**
 * Ensures the connected external wallet is on the same chain
 * the genlayer-js client is configured for before sending a transaction.
 * For local accounts this is a no-op.
 *
 * Reads the target chain from the genlayer client (not hardcoded),
 * so it works for localnet, studionet, and future testnets.
 */
export function useChainEnforcer() {
  const accountsStore = useAccountsStore();
  const wallet = useWallet();
  const genlayer = useGenlayer();

  const isExternalWallet = computed(
    () => accountsStore.selectedAccount?.type === 'external',
  );

  async function ensureCorrectChain() {
    if (!isExternalWallet.value) return;

    const provider = wallet.walletProvider.value;
    if (!provider?.request) return;

    const client = genlayer.client.value;
    if (!client?.chain) return;

    const targetChainId = client.chain.id;

    // Check current chain
    const currentChainHex = (await provider.request({
      method: 'eth_chainId',
    })) as string;
    const currentChainId = parseInt(currentChainHex, 16);

    if (currentChainId === targetChainId) return;

    const targetChainHex = numberToHex(targetChainId);

    try {
      await provider.request({
        method: 'wallet_switchEthereumChain',
        params: [{ chainId: targetChainHex }],
      });
    } catch (switchError: any) {
      // 4902 = chain not added to wallet, try adding it
      if (switchError.code === 4902) {
        const chain = client.chain;
        // Use the actual RPC URL the app is configured with,
        // not the SDK chain's baked-in default (which may be localhost)
        const rpcUrl = getRuntimeConfig(
          'VITE_JSON_RPC_SERVER_URL',
          chain.rpcUrls?.default?.http?.[0] ?? '',
        );
        await provider.request({
          method: 'wallet_addEthereumChain',
          params: [
            {
              chainId: targetChainHex,
              chainName: chain.name,
              nativeCurrency: chain.nativeCurrency,
              rpcUrls: [rpcUrl],
            },
          ],
        });
      } else if (switchError.code === 4001) {
        throw new Error(
          'Please switch to the GenLayer network to send transactions.',
        );
      } else {
        throw switchError;
      }
    }

    // Verify switch succeeded
    const newChainHex = (await provider.request({
      method: 'eth_chainId',
    })) as string;
    if (parseInt(newChainHex, 16) !== targetChainId) {
      throw new Error(
        'Please switch to the GenLayer network to send transactions.',
      );
    }
  }

  return { ensureCorrectChain };
}
