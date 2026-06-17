import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { effectScope, nextTick, ref } from 'vue';
import type { Address } from '@/types';

const walletState = {
  isConnected: ref(false),
  address: ref<Address | null>(null),
};

const accountsStore = {
  connectExternalWallet: vi.fn(),
  disconnectExternalWallet: vi.fn(),
  updateExternalWalletAddress: vi.fn(),
};

const recordWalletConnection = vi.fn(() => Promise.resolve());

vi.mock('@/stores', () => ({
  useAccountsStore: () => accountsStore,
}));

vi.mock('@/hooks/useWallet', () => ({
  useWallet: () => walletState,
}));

vi.mock('@/services/walletAnalytics', () => ({
  recordWalletConnection,
}));

describe('useWalletSync', () => {
  let scope: ReturnType<typeof effectScope>;

  beforeEach(() => {
    scope = effectScope();
    walletState.isConnected.value = false;
    walletState.address.value = null;
    accountsStore.connectExternalWallet.mockClear();
    accountsStore.disconnectExternalWallet.mockClear();
    accountsStore.updateExternalWalletAddress.mockClear();
    recordWalletConnection.mockClear();
  });

  afterEach(() => {
    scope.stop();
  });

  async function startWalletSync() {
    const { useWalletSync } = await import('@/hooks/useWalletSync');
    scope.run(() => useWalletSync());
  }

  it('records analytics when a wallet is connected', async () => {
    await startWalletSync();

    walletState.address.value = '0x1111111111111111111111111111111111111111';
    walletState.isConnected.value = true;
    await nextTick();

    expect(recordWalletConnection).toHaveBeenCalledWith(
      '0x1111111111111111111111111111111111111111',
    );
    expect(accountsStore.connectExternalWallet).toHaveBeenCalledWith(
      '0x1111111111111111111111111111111111111111',
      false,
    );
  });

  it('records analytics when the connected wallet address changes', async () => {
    await startWalletSync();

    walletState.address.value = '0x1111111111111111111111111111111111111111';
    walletState.isConnected.value = true;
    await nextTick();

    walletState.address.value = '0x2222222222222222222222222222222222222222';
    await nextTick();

    expect(recordWalletConnection).toHaveBeenCalledTimes(2);
    expect(recordWalletConnection).toHaveBeenLastCalledWith(
      '0x2222222222222222222222222222222222222222',
    );
  });

  it('does not record duplicate analytics for the same connected address', async () => {
    await startWalletSync();

    walletState.address.value = '0x1111111111111111111111111111111111111111';
    walletState.isConnected.value = true;
    await nextTick();

    walletState.address.value = '0x1111111111111111111111111111111111111111';
    await nextTick();

    expect(recordWalletConnection).toHaveBeenCalledTimes(1);
  });

  it('records again when the same wallet reconnects after disconnecting', async () => {
    await startWalletSync();

    walletState.address.value = '0x1111111111111111111111111111111111111111';
    walletState.isConnected.value = true;
    await nextTick();

    walletState.isConnected.value = false;
    await nextTick();

    walletState.isConnected.value = true;
    await nextTick();

    expect(recordWalletConnection).toHaveBeenCalledTimes(2);
  });
});
