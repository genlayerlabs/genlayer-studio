import { defineChain } from '@reown/appkit/networks';
import { markRaw } from 'vue';
import { localnet, studionet, testnetBradbury } from 'genlayer-js/chains';
import {
  getRuntimeConfig,
  getRuntimeConfigNumber,
} from '@/utils/runtimeConfig';
import type { GenLayerChain } from 'genlayer-js/types';

function getConfiguredStudioChain(): GenLayerChain {
  const configuredNetwork = getRuntimeConfig(
    'VITE_GENLAYER_NETWORK',
    'localnet',
  );
  return configuredNetwork === 'studionet'
    ? (studionet as GenLayerChain)
    : (localnet as GenLayerChain);
}

export function createGenlayerLocalnet() {
  const base = getConfiguredStudioChain();
  const defaultRpcUrl = base.rpcUrls?.default?.http?.[0] ?? '';
  const rpcUrl = getRuntimeConfig(
    'VITE_JSON_RPC_SERVER_URL',
    defaultRpcUrl || 'http://127.0.0.1:4000/api',
  );
  const chainId = getRuntimeConfigNumber('VITE_CHAIN_ID', base.id);
  const chainName = getRuntimeConfig('VITE_CHAIN_NAME', base.name);

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
