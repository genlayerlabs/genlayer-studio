import { watch } from 'vue';
import { useWallet } from './useWallet';
import { useAccountsStore } from '@/stores';

export function useWalletSync() {
  const wallet = useWallet();
  const accountsStore = useAccountsStore();
  let initialized = false;

  watch(
    [() => wallet.isConnected.value, () => wallet.address.value],
    ([isConnected, address], [wasConnected]) => {
      if (isConnected && address) {
        if (!initialized) {
          // Auto-reconnect on page load: add/update wallet but don't switch to it
          initialized = true;
          accountsStore.connectExternalWallet(address, false);
        } else {
          // User-initiated connect: switch to the external wallet
          accountsStore.connectExternalWallet(address);
        }
      } else if (wasConnected && !isConnected) {
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
