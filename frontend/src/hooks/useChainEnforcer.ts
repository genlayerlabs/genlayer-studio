import { computed } from 'vue';
import { numberToHex } from 'viem';
import { useAccountsStore } from '@/stores';
import { useWallet } from './useWallet';
import { useGenlayer } from './useGenlayer';
import {
  getRuntimeConfig,
  getRuntimeConfigNumber,
} from '@/utils/runtimeConfig';

/**
 * Ensures the connected external wallet is on the correct chain
 * before sending a transaction. For local accounts this is a no-op.
 *
 * Uses VITE_CHAIN_ID for the target chain (each Studio instance has
 * its own chain ID), falling back to the SDK client's chain ID.
 * Uses VITE_JSON_RPC_SERVER_URL for the RPC URL when adding the
 * network to MetaMask.
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

    // Use per-deployment chain ID (each Studio instance has its own),
    // falling back to SDK chain ID
    const targetChainId = getRuntimeConfigNumber(
      'VITE_CHAIN_ID',
      client.chain.id,
    );

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
      if (switchError.code === 4902) {
        const chain = client.chain;
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
