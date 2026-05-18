import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { useNetworkStore } from '@/stores/network';

describe('useNetworkStore', () => {
  beforeEach(() => {
    localStorage.removeItem('networkStore.currentNetwork');
    delete window.__RUNTIME_CONFIG__;
    vi.unstubAllEnvs();
    setActivePinia(createPinia());
  });

  afterEach(() => {
    localStorage.removeItem('networkStore.currentNetwork');
    delete window.__RUNTIME_CONFIG__;
    vi.unstubAllEnvs();
  });

  it('keeps the deployment-configured Studio network selectable after switching to Bradbury', () => {
    window.__RUNTIME_CONFIG__ = {
      VITE_GENLAYER_NETWORK: 'studionet',
    };

    const store = useNetworkStore();

    expect(store.currentNetwork).toBe('studionet');
    store.setCurrentNetwork('testnetBradbury');

    expect(store.availableNetworks.map((network) => network.name)).toEqual([
      'studionet',
      'testnetBradbury',
    ]);
    expect(
      store.availableNetworks.find((network) => network.name === 'studionet'),
    ).toMatchObject({
      label: 'Genlayer Studio Network',
      chainId: 61999,
      isStudio: true,
    });
  });

  it('uses runtime Studio chain overrides in the active chain and selector option', () => {
    window.__RUNTIME_CONFIG__ = {
      VITE_GENLAYER_NETWORK: 'studionet',
      VITE_CHAIN_ID: '77777',
      VITE_CHAIN_NAME: 'Genlayer Custom Studio',
      VITE_JSON_RPC_SERVER_URL: 'https://studio.example.com/api',
    };

    const store = useNetworkStore();
    const studioOption = store.availableNetworks.find(
      (network) => network.name === 'studionet',
    );

    expect(store.chainId).toBe(77777);
    expect(store.chainName).toBe('Genlayer Custom Studio');
    expect(store.rpcUrl).toBe('https://studio.example.com/api');
    expect(studioOption).toMatchObject({
      label: 'Genlayer Custom Studio',
      chainId: 77777,
    });
  });
});
