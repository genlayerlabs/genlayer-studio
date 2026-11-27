import { defineStore } from 'pinia';
import { computed, ref, markRaw } from 'vue';
import {
  localnet,
  studionet,
  testnetAsimov,
  testnetBradbury,
  testnetClarke,
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
  | 'testnetBradbury'
  | 'testnetClarke';

const CHAINS: Record<NetworkName, GenLayerChain> = {
  localnet: localnet as GenLayerChain,
  studionet: studionet as GenLayerChain,
  testnetAsimov: testnetAsimov as GenLayerChain,
  testnetBradbury: testnetBradbury as GenLayerChain,
  testnetClarke: testnetClarke as GenLayerChain,
};

const STORAGE_KEY = 'networkStore.currentNetwork';

// The Bradbury preset points at the rollup RPC, which serves no gen_* methods.
const BRADBURY_NODE_RPC_URL = 'https://rpc.bradbury.genlayer.com';

// Clarke has no default node: its preset also points at the rollup RPC, so the
// deployment has to name the Clarke full node in `VITE_CLARKE_NODE_RPC_URL`.
function getClarkeNodeRpcUrl(): string {
  return getRuntimeConfig('VITE_CLARKE_NODE_RPC_URL', '');
}

// One deployment-configured Studio network plus the testnets are
// runtime-selectable. Asimov shares Bradbury's chain ID so it is not exposed
// as a separate dropdown entry; reachable only via a deployment-time
// `VITE_GENLAYER_NETWORK` override. Clarke is offered only once it has a node.
function getSelectableTestnetNetworks(): NetworkName[] {
  return getClarkeNodeRpcUrl()
    ? ['testnetBradbury', 'testnetClarke']
    : ['testnetBradbury'];
}

/**
 * Identifies whose records a stored contract or transaction is. Clarke and
 * Bradbury are two deployments on the same chain, so the chain ID alone does
 * not tell them apart: Clarke records also carry the network name. Records of
 * every other network carry no name, as they did before Clarke existed.
 */
export interface RecordScope {
  chainId: number;
  network?: string;
}

export function getRecordScope(
  network: NetworkName,
  chainId: number,
): RecordScope {
  return network === 'testnetClarke' ? { chainId, network } : { chainId };
}

// A record saved without a chain belongs to whichever network is asking.
export function isRecordInScope(
  record: { chainId?: number; network?: string },
  scope: RecordScope,
): boolean {
  if (record.chainId === undefined) return true;
  return record.chainId === scope.chainId && record.network === scope.network;
}

function getConfiguredNetwork(): NetworkName {
  const fromEnv = getRuntimeConfig(
    'VITE_GENLAYER_NETWORK',
    'localnet',
  ) as NetworkName;

  return fromEnv in CHAINS ? fromEnv : 'localnet';
}

function getSelectableStudioNetwork(): NetworkName {
  const configured = getConfiguredNetwork();
  return CHAINS[configured].isStudio ? configured : 'localnet';
}

function resolveNetworkChain(name: NetworkName): GenLayerChain {
  const base = CHAINS[name];
  if (!base.isStudio) return base as GenLayerChain;

  const defaultRpcUrl = base.rpcUrls?.default?.http?.[0] ?? '';
  const runtimeRpcUrl = getRuntimeConfig(
    'VITE_JSON_RPC_SERVER_URL',
    defaultRpcUrl,
  );

  return markRaw({
    ...base,
    id: getRuntimeConfigNumber('VITE_CHAIN_ID', base.id),
    name: getRuntimeConfig('VITE_CHAIN_NAME', base.name),
    rpcUrls: {
      ...base.rpcUrls,
      default: {
        ...base.rpcUrls.default,
        http: runtimeRpcUrl ? [runtimeRpcUrl] : [],
      },
    },
  }) as GenLayerChain;
}

function readInitialNetwork(): NetworkName {
  const persisted = localStorage.getItem(STORAGE_KEY) as NetworkName | null;
  if (persisted && persisted in CHAINS) return persisted;

  return getConfiguredNetwork();
}

export const useNetworkStore = defineStore('networkStore', () => {
  const currentNetwork = ref<NetworkName>(readInitialNetwork());

  const isLocked = computed(() =>
    getRuntimeConfigBoolean('VITE_LOCK_NETWORK', false),
  );

  const chain = computed<GenLayerChain>(() => {
    return resolveNetworkChain(currentNetwork.value);
  });

  const chainId = computed(() => chain.value.id);
  const isStudio = computed(() => Boolean(chain.value.isStudio));
  const chainName = computed(() => chain.value.name);
  const recordScope = computed<RecordScope>(() =>
    getRecordScope(currentNetwork.value, chainId.value),
  );

  const rpcUrl = computed(() => {
    // On Studio, operators can override the RPC URL (e.g. hosted deployment).
    if (isStudio.value) {
      return getRuntimeConfig(
        'VITE_JSON_RPC_SERVER_URL',
        chain.value.rpcUrls?.default?.http?.[0] ?? 'http://127.0.0.1:4000/api',
      );
    }
    if (currentNetwork.value === 'testnetBradbury') {
      return getRuntimeConfig(
        'VITE_BRADBURY_NODE_RPC_URL',
        BRADBURY_NODE_RPC_URL,
      );
    }
    if (currentNetwork.value === 'testnetClarke' && getClarkeNodeRpcUrl()) {
      return getClarkeNodeRpcUrl();
    }
    return chain.value.rpcUrls?.default?.http?.[0] ?? '';
  });

  // A testnet node RPC is read-only: fees and sending go through the chain RPC.
  const transactionRpcUrl = computed(() =>
    isStudio.value
      ? rpcUrl.value
      : (chain.value.rpcUrls?.default?.http?.[0] ?? rpcUrl.value),
  );

  const wsUrl = computed<string | null>(() => {
    // WebSocket push events only exist on Studio backends.
    if (!isStudio.value) return null;
    return getRuntimeConfig('VITE_WS_SERVER_URL', 'ws://localhost:4000');
  });

  const availableNetworks = computed<
    { name: NetworkName; label: string; isStudio: boolean; chainId: number }[]
  >(() => {
    const selectableNetworks: NetworkName[] = [
      getSelectableStudioNetwork(),
      ...getSelectableTestnetNetworks(),
    ];

    const list = selectableNetworks.map((name) => {
      const c = resolveNetworkChain(name);
      return {
        name,
        label: c.name,
        isStudio: Boolean(c.isStudio),
        chainId: c.id,
      };
    });

    // If the user has a persisted network outside the selectable list, surface
    // it so they can see where they are before choosing a supported target.
    if (!list.some((n) => n.name === currentNetwork.value)) {
      const c = resolveNetworkChain(currentNetwork.value);
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
    recordScope,
    rpcUrl,
    transactionRpcUrl,
    wsUrl,
    availableNetworks,
    setCurrentNetwork,
  };
});
