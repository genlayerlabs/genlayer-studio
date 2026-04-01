import { computed } from 'vue';
import { numberToHex } from 'viem';
import { useAccountsStore } from '@/stores';
import { useWallet } from './useWallet';
import { createGenlayerLocalnet } from './useNetworks';
import { appKitReady } from './useAppKit';

let genlayerLocalnet: ReturnType<typeof createGenlayerLocalnet> | null = null;

function getNetwork() {
  if (!genlayerLocalnet && appKitReady) {
    genlayerLocalnet = createGenlayerLocalnet();
  }
  return genlayerLocalnet;
}

/**
 * Ensures the connected external wallet is on the GenLayer network
 * before sending a transaction. For local accounts this is a no-op.
 *
 * Uses wallet_switchEthereumChain / wallet_addEthereumChain directly
 * via the EIP-1193 provider (Rally2 pattern).
 */
export function useChainEnforcer() {
  const accountsStore = useAccountsStore();
  const wallet = useWallet();

  const isExternalWallet = computed(
    () => accountsStore.selectedAccount?.type === 'external',
  );

  async function ensureCorrectChain() {
    if (!isExternalWallet.value) return;

    const network = getNetwork();
    if (!network) return;

    const provider = wallet.walletProvider.value;
    if (!provider?.request) return;

    // Check current chain
    const currentChainHex = (await provider.request({
      method: 'eth_chainId',
    })) as string;
    const currentChainId = parseInt(currentChainHex, 16);

    if (currentChainId === network.id) return;

    const targetChainHex = numberToHex(network.id);

    try {
      await provider.request({
        method: 'wallet_switchEthereumChain',
        params: [{ chainId: targetChainHex }],
      });
    } catch (switchError: any) {
      // 4902 = chain not added to wallet, try adding it
      if (switchError.code === 4902) {
        const rpcUrl =
          network.rpcUrls?.default?.http?.[0] ?? 'http://127.0.0.1:4000/api';
        await provider.request({
          method: 'wallet_addEthereumChain',
          params: [
            {
              chainId: targetChainHex,
              chainName: network.name,
              nativeCurrency: network.nativeCurrency,
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
    if (parseInt(newChainHex, 16) !== network.id) {
      throw new Error(
        'Please switch to the GenLayer network to send transactions.',
      );
    }
  }

  return { ensureCorrectChain };
}
