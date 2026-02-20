import { defineChain } from '@reown/appkit/networks';
import { markRaw } from 'vue';

export const genlayerLocalnet = markRaw(
  defineChain({
    id: 61999,
    caipNetworkId: 'eip155:61999',
    chainNamespace: 'eip155',
    name: 'GenLayer Localnet',
    nativeCurrency: {
      name: 'GEN',
      symbol: 'GEN',
      decimals: 18,
    },
    rpcUrls: {
      default: { http: ['http://127.0.0.1:8545'] },
    },
  }),
);
