import { watch } from 'vue';
import { useWallet } from './useWallet';
import { useAccountsStore } from '@/stores';
import { recordWalletConnection } from '@/services/walletAnalytics';
import type { Address } from '@/types';

export function useWalletSync() {
  const wallet = useWallet();
  const accountsStore = useAccountsStore();
  let initialized = false;
  let lastRecordedAddress: string | null = null;

  const recordAnalytics = (address: Address) => {
    const normalizedAddress = address.toLowerCase();
    if (lastRecordedAddress === normalizedAddress) return;

    lastRecordedAddress = normalizedAddress;
    void recordWalletConnection(address).catch((error) => {
      console.debug('Wallet connection analytics failed', error);
    });
  };

  watch(
    [() => wallet.isConnected.value, () => wallet.address.value],
    ([isConnected, address], [wasConnected]) => {
      if (isConnected && address) {
        recordAnalytics(address);
        if (!initialized) {
          // Auto-reconnect on page load: add/update wallet but don't switch to it
          initialized = true;
          accountsStore.connectExternalWallet(address, false);
        } else {
          // User-initiated connect: switch to the external wallet
          accountsStore.connectExternalWallet(address);
        }
      } else if (wasConnected && !isConnected) {
        lastRecordedAddress = null;
        accountsStore.disconnectExternalWallet();
      }
    },
    { immediate: true },
  );

  watch(
    () => wallet.address.value,
    (newAddress, oldAddress) => {
      if (
        wallet.isConnected.value &&
        newAddress &&
        oldAddress &&
        newAddress !== oldAddress
      ) {
        accountsStore.updateExternalWalletAddress(newAddress);
      }
    },
  );
}
