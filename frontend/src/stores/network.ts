import { defineStore } from 'pinia';
import { computed, ref, markRaw } from 'vue';
import {
  localnet,
  studionet,
  testnetAsimov,
  testnetBradbury,
} from 'genlayer-js/chains';
import type { GenLayerChain } from 'genlayer-js/types';
import {
  getRuntimeConfig,
  getRuntimeConfigBoolean,
  getRuntimeConfigNumber,
} from '@/utils/runtimeConfig';

export type NetworkName =
  | 'localnet'
  | 'studionet'
  | 'testnetAsimov'
  | 'testnetBradbury';

const CHAINS: Record<NetworkName, GenLayerChain> = {
  localnet: localnet as GenLayerChain,
  studionet: studionet as GenLayerChain,
  testnetAsimov: testnetAsimov as GenLayerChain,
  testnetBradbury: testnetBradbury as GenLayerChain,
};

const STORAGE_KEY = 'networkStore.currentNetwork';

// v1 ships Studio + Bradbury as runtime-selectable options. Asimov shares
// Bradbury's chain ID so it is not exposed as a separate dropdown entry;
// reachable only via a deployment-time `VITE_GENLAYER_NETWORK` override.
const SELECTABLE_NETWORKS: NetworkName[] = ['localnet', 'testnetBradbury'];

function readInitialNetwork(): NetworkName {
  const persisted = localStorage.getItem(STORAGE_KEY) as NetworkName | null;
  if (persisted && persisted in CHAINS) return persisted;

  const fromEnv = getRuntimeConfig(
    'VITE_GENLAYER_NETWORK',
    'localnet',
  ) as NetworkName;
  if (fromEnv in CHAINS) return fromEnv;

  return 'localnet';
}

export const useNetworkStore = defineStore('networkStore', () => {
  const currentNetwork = ref<NetworkName>(readInitialNetwork());

  const isLocked = computed(() =>
    getRuntimeConfigBoolean('VITE_LOCK_NETWORK', false),
  );

  const chain = computed<GenLayerChain>(() => {
    const base = CHAINS[currentNetwork.value];
    if (base.isStudio) {
      // Per-deployment Studio chain-ID override (each Studio instance picks its own).
      const override = getRuntimeConfigNumber('VITE_CHAIN_ID', 0);
      if (override > 0 && override !== base.id) {
        return markRaw({ ...base, id: override }) as GenLayerChain;
      }
    }
    return markRaw(base) as GenLayerChain;
  });

  const chainId = computed(() => chain.value.id);
  const isStudio = computed(() => Boolean(chain.value.isStudio));
  const chainName = computed(() => chain.value.name);

  const rpcUrl = computed(() => {
    // On Studio, operators can override the RPC URL (e.g. hosted deployment).
    if (isStudio.value) {
      return getRuntimeConfig(
        'VITE_JSON_RPC_SERVER_URL',
        chain.value.rpcUrls?.default?.http?.[0] ?? 'http://127.0.0.1:4000/api',
      );
    }
    return chain.value.rpcUrls?.default?.http?.[0] ?? '';
  });

  const wsUrl = computed<string | null>(() => {
    // WebSocket push events only exist on Studio backends.
    if (!isStudio.value) return null;
    return getRuntimeConfig('VITE_WS_SERVER_URL', 'ws://localhost:4000');
  });

  const availableNetworks = computed<
    { name: NetworkName; label: string; isStudio: boolean; chainId: number }[]
  >(() => {
    const list = SELECTABLE_NETWORKS.map((name) => {
      const c = CHAINS[name];
      return {
        name,
        label: c.name,
        isStudio: Boolean(c.isStudio),
        chainId: c.id,
      };
    });

    // If the operator pinned a network outside the selectable list (e.g. Asimov
    // via VITE_GENLAYER_NETWORK), surface it so the user can at least see where
    // they are — but they cannot switch away from it via the dropdown.
    if (!list.some((n) => n.name === currentNetwork.value)) {
      const c = CHAINS[currentNetwork.value];
      list.push({
        name: currentNetwork.value,
        label: c.name,
        isStudio: Boolean(c.isStudio),
        chainId: c.id,
      });
    }

    return list;
  });

  function setCurrentNetwork(name: NetworkName) {
    if (!(name in CHAINS)) {
      throw new Error(`Unknown network: ${name}`);
    }
    if (name === currentNetwork.value) return;
    currentNetwork.value = name;
    try {
      localStorage.setItem(STORAGE_KEY, name);
    } catch {
      // localStorage may be unavailable (SSR / privacy mode); ignore.
    }
  }

  return {
    currentNetwork,
    isLocked,
    chain,
    chainId,
    chainName,
    isStudio,
    rpcUrl,
    wsUrl,
    availableNetworks,
    setCurrentNetwork,
  };
});
