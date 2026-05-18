import { describe, it, expect, beforeEach, vi } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import { useConfig } from '@/hooks';
import { useNetworkStore } from '@/stores/network';

describe('useConfig', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.stubEnv('VITE_IS_HOSTED', 'false');
  });

  it('returns true for canUpdateValidators in a non-hosted Studio environment', () => {
    vi.stubEnv('VITE_IS_HOSTED', 'false');
    useNetworkStore().setCurrentNetwork('localnet');

    const { canUpdateValidators } = useConfig();
    expect(canUpdateValidators.value).toBe(true);
  });

  it('returns false for canUpdateValidators when hosted', () => {
    vi.stubEnv('VITE_IS_HOSTED', 'true');
    useNetworkStore().setCurrentNetwork('localnet');

    const { canUpdateValidators } = useConfig();
    expect(canUpdateValidators.value).toBe(false);
  });

  it('returns false for canUpdateValidators when on a non-Studio network', () => {
    vi.stubEnv('VITE_IS_HOSTED', 'false');
    useNetworkStore().setCurrentNetwork('testnetBradbury');

    const { canUpdateValidators } = useConfig();
    expect(canUpdateValidators.value).toBe(false);
  });

  it('exposes isStudioNetwork that reflects the current network', () => {
    const store = useNetworkStore();
    const { isStudioNetwork } = useConfig();

    store.setCurrentNetwork('localnet');
    expect(isStudioNetwork.value).toBe(true);

    store.setCurrentNetwork('testnetBradbury');
    expect(isStudioNetwork.value).toBe(false);
  });
});
