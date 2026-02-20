import { defineChain } from '@reown/appkit/networks';
import { markRaw } from 'vue';
import { getRuntimeConfig } from '@/utils/runtimeConfig';

export function createGenlayerLocalnet() {
  const rpcUrl = getRuntimeConfig(
    'VITE_JSON_RPC_SERVER_URL',
    'http://127.0.0.1:4000/api',
  );

  return markRaw(
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
        default: { http: [rpcUrl] },
      },
    }),
  );
}
