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

  it('uses the canonical local Studio chain by default', () => {
    const store = useNetworkStore();

    expect(store.currentNetwork).toBe('localnet');
    expect(store.chainId).toBe(61127);
    expect(store.isStudio).toBe(true);
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

  it('sends Bradbury GenLayer RPC calls to the node and keeps the rollup RPC on the chain', () => {
    const store = useNetworkStore();
    store.setCurrentNetwork('testnetBradbury');

    expect(store.rpcUrl).toBe('https://rpc.bradbury.genlayer.com');
    expect(store.transactionRpcUrl).toBe(
      'https://rpc.testnet-chain.genlayer.com',
    );
    expect(store.chain.rpcUrls.default.http).toEqual([
      'https://rpc.testnet-chain.genlayer.com',
    ]);
    expect(store.chain.consensusMainContract?.address).toBe(
      '0x0f4F0e05475723790E4a2ed0598370783C300926',
    );
  });

  it('lets the deployment override the Bradbury node RPC', () => {
    window.__RUNTIME_CONFIG__ = {
      VITE_BRADBURY_NODE_RPC_URL: 'https://node.example.com',
    };

    const store = useNetworkStore();
    store.setCurrentNetwork('testnetBradbury');

    expect(store.rpcUrl).toBe('https://node.example.com');
    expect(store.transactionRpcUrl).toBe(
      'https://rpc.testnet-chain.genlayer.com',
    );
  });

  it('offers Clarke only when the deployment names its node', () => {
    const withoutNode = useNetworkStore();
    expect(withoutNode.availableNetworks.map((n) => n.name)).toEqual([
      'localnet',
      'testnetBradbury',
    ]);

    window.__RUNTIME_CONFIG__ = {
      VITE_CLARKE_NODE_RPC_URL: 'https://clarke-node.example.com',
    };
    setActivePinia(createPinia());

    expect(useNetworkStore().availableNetworks.map((n) => n.name)).toEqual([
      'localnet',
      'testnetBradbury',
      'testnetClarke',
    ]);
  });

  it('sends Clarke GenLayer RPC calls to its node and uses the Clarke contracts', () => {
    window.__RUNTIME_CONFIG__ = {
      VITE_CLARKE_NODE_RPC_URL: 'https://clarke-node.example.com',
    };

    const store = useNetworkStore();
    store.setCurrentNetwork('testnetClarke');

    expect(store.rpcUrl).toBe('https://clarke-node.example.com');
    expect(store.transactionRpcUrl).toBe(
      'https://rpc.testnet-chain.genlayer.com',
    );
    expect(store.chain.consensusMainContract?.address).toBe(
      '0xa44222E0beDaB12329557499ae6E555b1c9D02ee',
    );
  });

  it('tells Clarke records apart from Bradbury ones on the shared chain', () => {
    const store = useNetworkStore();

    store.setCurrentNetwork('testnetBradbury');
    const bradbury = store.recordScope;
    store.setCurrentNetwork('testnetClarke');
    const clarke = store.recordScope;

    expect(clarke.chainId).toBe(bradbury.chainId);
    expect(bradbury.network).toBeUndefined();
    expect(clarke.network).toBe('testnetClarke');
  });

  it('sends Studio transactions through the Studio RPC', () => {
    const store = useNetworkStore();

    expect(store.isStudio).toBe(true);
    expect(store.transactionRpcUrl).toBe(store.rpcUrl);
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
