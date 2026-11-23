import { computed } from 'vue';
import { numberToHex } from 'viem';
import { useAccountsStore } from '@/stores';
import { useNetworkStore } from '@/stores/network';
import { useWallet } from './useWallet';
import { getRuntimeConfig } from '@/utils/runtimeConfig';

export class ChainMismatchError extends Error {
  constructor(message: string, options?: { cause?: unknown }) {
    super(message, options);
    this.name = 'ChainMismatchError';
  }
}

/**
 * Human-readable explanation of a wallet/Studio chain mismatch, shown to the
 * user when the wallet cannot be (or refuses to be) switched to the network
 * Studio is configured for.
 */
export function chainMismatchMessage(
  walletChainId: number,
  targetChainId: number,
  targetChainName: string,
): string {
  const walletChain = Number.isNaN(walletChainId)
    ? 'an unknown chain'
    : `chain ID ${walletChainId}`;
  return (
    `Your wallet is connected to ${walletChain}, but ${targetChainName} ` +
    `uses chain ID ${targetChainId}. Switch your wallet to ${targetChainName} ` +
    `(chain ID ${targetChainId}) to send transactions.`
  );
}

/**
 * Ensures the connected external wallet is on the correct chain
 * before sending a transaction. For local accounts this is a no-op.
 *
 * Chain info comes from the network store; an explicit `target` can be
 * supplied when switching network via the UI selector (so we enforce
 * the new chain before the SDK client re-inits).
 */
export function useChainEnforcer() {
  const accountsStore = useAccountsStore();
  const networkStore = useNetworkStore();
  const wallet = useWallet();

  const isExternalWallet = computed(
    () => accountsStore.selectedAccount?.type === 'external',
  );

  async function ensureCorrectChain(target?: {
    chainId: number;
    chainName: string;
    rpcUrl: string;
    nativeCurrency?: {
      name: string;
      symbol: string;
      decimals: number;
    };
  }): Promise<void> {
    if (!isExternalWallet.value) return;

    const provider = wallet.walletProvider.value;
    if (!provider?.request) return;

    const chain = networkStore.chain;
    const targetChainId = target?.chainId ?? networkStore.chainId;
    const defaultHttpRpc = chain.rpcUrls?.default?.http?.[0] ?? '';
    const rpcUrl =
      target?.rpcUrl ??
      (networkStore.isStudio
        ? getRuntimeConfig('VITE_JSON_RPC_SERVER_URL', defaultHttpRpc)
        : defaultHttpRpc);
    const chainName =
      target?.chainName ??
      (networkStore.isStudio
        ? getRuntimeConfig('VITE_CHAIN_NAME', chain.name)
        : chain.name);
    const nativeCurrency = target?.nativeCurrency ?? chain.nativeCurrency;

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
      if (switchError?.code === 4902) {
        try {
          await provider.request({
            method: 'wallet_addEthereumChain',
            params: [
              {
                chainId: targetChainHex,
                chainName,
                nativeCurrency,
                rpcUrls: [rpcUrl],
              },
            ],
          });
        } catch (addError: any) {
          throw new ChainMismatchError(
            chainMismatchMessage(currentChainId, targetChainId, chainName),
            { cause: addError },
          );
        }
      } else {
        // User rejection (4001) or any other wallet failure: the wallet is
        // still on the wrong chain, so explain that and keep the cause.
        throw new ChainMismatchError(
          chainMismatchMessage(currentChainId, targetChainId, chainName),
          { cause: switchError },
        );
      }
    }

    // Re-read the wallet's chain: some wallets resolve the switch request
    // without actually changing network (or the user changed it back).
    const newChainHex = (await provider.request({
      method: 'eth_chainId',
    })) as string;
    const newChainId = parseInt(newChainHex, 16);
    if (newChainId !== targetChainId) {
      throw new ChainMismatchError(
        chainMismatchMessage(newChainId, targetChainId, chainName),
      );
    }
  }

  return { ensureCorrectChain, isExternalWallet };
}
