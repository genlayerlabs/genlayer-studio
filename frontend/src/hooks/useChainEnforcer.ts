import { computed } from 'vue';
import { numberToHex } from 'viem';
import { useAccountsStore } from '@/stores';
import { useWallet } from './useWallet';
import { createGenlayerLocalnet } from './useNetworks';
import { appKitReady } from './useAppKit';

const genlayerLocalnet = appKitReady ? createGenlayerLocalnet() : null;

/**
 * Ensures the connected external wallet is on the GenLayer network
 * before sending a transaction. For local accounts this is a no-op.
 *
 * Uses wallet_switchEthereumChain / wallet_addEthereumChain directly
 * via the EIP-1193 provider (Rally2 pattern).
 */
export function useChainEnforcer() {
  if (!appKitReady || !genlayerLocalnet) {
    return { ensureCorrectChain: async () => {} };
  }

  const accountsStore = useAccountsStore();
  const wallet = useWallet();

  const isExternalWallet = computed(
    () => accountsStore.selectedAccount?.type === 'external',
  );

  async function ensureCorrectChain() {
    if (!isExternalWallet.value) return;

    const provider = wallet.walletProvider.value;
    if (!provider?.request) return;

    // Check current chain
    const currentChainHex = (await provider.request({
      method: 'eth_chainId',
    })) as string;
    const currentChainId = parseInt(currentChainHex, 16);

    if (currentChainId === genlayerLocalnet!.id) return;

    const targetChainHex = numberToHex(genlayerLocalnet!.id);

    try {
      await provider.request({
        method: 'wallet_switchEthereumChain',
        params: [{ chainId: targetChainHex }],
      });
    } catch (switchError: any) {
      // 4902 = chain not added to wallet, try adding it
      if (switchError.code === 4902) {
        const rpcUrl =
          genlayerLocalnet!.rpcUrls?.default?.http?.[0] ??
          'http://127.0.0.1:4000/api';
        await provider.request({
          method: 'wallet_addEthereumChain',
          params: [
            {
              chainId: targetChainHex,
              chainName: genlayerLocalnet!.name,
              nativeCurrency: genlayerLocalnet!.nativeCurrency,
              rpcUrls: [rpcUrl],
            },
          ],
        });
      } else if (switchError.code === 4001) {
        // User rejected
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
    if (parseInt(newChainHex, 16) !== genlayerLocalnet!.id) {
      throw new Error(
        'Please switch to the GenLayer network to send transactions.',
      );
    }
  }

  return { ensureCorrectChain };
}
