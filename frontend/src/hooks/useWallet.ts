import {
  useAppKit,
  useAppKitAccount,
  useAppKitProvider,
  useDisconnect,
} from '@reown/appkit/vue';
import { computed, ref, toRefs } from 'vue';
import { getAddress } from 'viem';
import type { EIP1193Provider } from 'viem';
import type { Address } from '@/types';
import { appKitReady } from './useAppKit';

export function useWallet() {
  if (!appKitReady) {
    return {
      isConnected: ref(false),
      address: ref<Address | null>(null),
      walletProvider: ref<EIP1193Provider | undefined>(undefined),
      connect: () => {
        console.warn('AppKit not initialized. Set VITE_APPKIT_PROJECT_ID.');
      },
      disconnect: async () => {},
    };
  }

  const accountData = useAppKitAccount();
  const providerData = useAppKitProvider<EIP1193Provider>('eip155');
  const { walletProvider: appKitWalletProvider } = toRefs(providerData);
  const { disconnect: appKitDisconnect } = useDisconnect();
  const appKit = useAppKit();

  const isConnected = computed(() => accountData.value.isConnected);

  const address = computed<Address | null>(() => {
    if (!accountData.value.address) return null;
    try {
      return getAddress(accountData.value.address) as Address;
    } catch {
      return null;
    }
  });

  const walletProvider = computed<EIP1193Provider | undefined>(
    () => appKitWalletProvider.value,
  );

  function connect() {
    appKit.open();
  }

  async function disconnect() {
    await appKitDisconnect();
  }

  return {
    isConnected,
    address,
    walletProvider,
    connect,
    disconnect,
  };
}
