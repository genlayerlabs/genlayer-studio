import { watch } from 'vue';
import { useWallet } from './useWallet';
import { useAccountsStore } from '@/stores';

export function useWalletSync() {
  const wallet = useWallet();
  const accountsStore = useAccountsStore();

  watch(
    [() => wallet.isConnected.value, () => wallet.address.value],
    ([isConnected, address], [wasConnected]) => {
      if (isConnected && address) {
        accountsStore.connectExternalWallet(address);
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
