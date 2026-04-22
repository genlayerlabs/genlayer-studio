import { defineChain } from '@reown/appkit/networks';
import { markRaw } from 'vue';
import { testnetBradbury } from 'genlayer-js/chains';
import {
  getRuntimeConfig,
  getRuntimeConfigNumber,
} from '@/utils/runtimeConfig';

export function createGenlayerLocalnet() {
  const rpcUrl = getRuntimeConfig(
    'VITE_JSON_RPC_SERVER_URL',
    'http://127.0.0.1:4000/api',
  );
  const chainId = getRuntimeConfigNumber('VITE_CHAIN_ID', 61999);
  const chainName = getRuntimeConfig('VITE_CHAIN_NAME', 'GenLayer Localnet');

  return markRaw(
    defineChain({
      id: chainId,
      caipNetworkId: `eip155:${chainId}`,
      chainNamespace: 'eip155',
      name: chainName,
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

export function createGenlayerBradbury() {
  return markRaw(
    defineChain({
      id: testnetBradbury.id,
      caipNetworkId: `eip155:${testnetBradbury.id}`,
      chainNamespace: 'eip155',
      name: testnetBradbury.name,
      nativeCurrency: testnetBradbury.nativeCurrency,
      rpcUrls: {
        default: { http: [...testnetBradbury.rpcUrls.default.http] },
      },
      blockExplorers: testnetBradbury.blockExplorers,
    }),
  );
}
