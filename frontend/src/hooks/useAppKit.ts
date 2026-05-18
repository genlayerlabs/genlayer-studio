import { shallowRef } from 'vue';
import { WagmiAdapter } from '@reown/appkit-adapter-wagmi';
import { createAppKit } from '@reown/appkit/vue';
import type { AppKitNetwork } from '@reown/appkit/networks';
import { mainnet, sepolia } from '@reown/appkit/networks';
import { custom } from 'viem';
import { getRuntimeConfig } from '@/utils/runtimeConfig';
import { createGenlayerLocalnet, createGenlayerBradbury } from './useNetworks';

// GenLayer's Go RPC server rejects requests missing `jsonrpc` or `id`. Some
// transport paths in viem/Wagmi/Reown surface a body without `id` (the field
// is added by the http transport, but batching/retry can drop it on certain
// paths). Wrap our chain RPCs in a custom transport that guarantees both.
const RPC_REQUEST_TIMEOUT_MS = 30_000;
let rpcIdCounter = 1;

function getRpcErrorPreview(text: string) {
  return text.length > 200 ? `${text.slice(0, 200)}...` : text;
}

function makeStrictJsonRpcTransport(rpcUrl: string) {
  return custom({
    async request({ method, params }) {
      const id = rpcIdCounter++;
      const controller = new AbortController();
      const timeout = window.setTimeout(
        () => controller.abort(),
        RPC_REQUEST_TIMEOUT_MS,
      );

      let res: Response;
      try {
        res = await fetch(rpcUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ jsonrpc: '2.0', id, method, params }),
          signal: controller.signal,
        });
      } catch (cause: any) {
        const err: any = new Error(
          cause?.name === 'AbortError'
            ? `RPC request timed out after ${RPC_REQUEST_TIMEOUT_MS}ms`
            : (cause?.message ?? 'RPC request failed'),
        );
        err.cause = cause;
        throw err;
      } finally {
        window.clearTimeout(timeout);
      }

      if (!res.ok) {
        const text = await res.text().catch(() => '');
        throw new Error(
          `RPC HTTP ${res.status} ${res.statusText}${
            text ? `: ${getRpcErrorPreview(text)}` : ''
          }`,
        );
      }

      let json: any;
      try {
        json = await res.json();
      } catch (cause: any) {
        const err: any = new Error(
          `RPC response was not valid JSON${
            cause?.message ? `: ${cause.message}` : ''
          }`,
        );
        err.cause = cause;
        throw err;
      }

      if (json.error) {
        const err: any = new Error(json.error.message ?? 'RPC error');
        err.code = json.error.code;
        err.data = json.error.data;
        throw err;
      }
      return json.result;
    },
  });
}

function getDefaultRpcUrl(network: AppKitNetwork) {
  const rpcUrl = network.rpcUrls.default.http[0];
  if (!rpcUrl) {
    throw new Error(`Missing default RPC URL for ${network.name}`);
  }
  return rpcUrl;
}

export const wagmiAdapterRef = shallowRef<WagmiAdapter>();
export let appKitReady = false;

class GenlayerWagmiAdapter extends WagmiAdapter {
  private connectPromises = new Map<string, Promise<any>>();

  override async connect(params: any) {
    const connectKey = `${params.id}:${params.address ?? ''}`;
    const inFlight = this.connectPromises.get(connectKey);
    if (inFlight) return await inFlight;

    const promise = super.connect(
      params.chainId !== undefined ? { ...params, chainId: undefined } : params,
    );
    this.connectPromises.set(connectKey, promise);
    try {
      return await promise;
    } finally {
      this.connectPromises.delete(connectKey);
    }
  }
}

export async function initAppKit() {
  const projectId = getRuntimeConfig('VITE_APPKIT_PROJECT_ID', '');

  if (!projectId) {
    console.warn(
      'VITE_APPKIT_PROJECT_ID is not set. External wallet connection will not work.',
    );
    return;
  }

  const genlayerLocalnet = createGenlayerLocalnet();
  const genlayerBradbury = createGenlayerBradbury();

  const networks: [AppKitNetwork, ...AppKitNetwork[]] = [
    genlayerLocalnet,
    genlayerBradbury,
    mainnet,
    sepolia,
  ];

  const adapter = new GenlayerWagmiAdapter({
    projectId,
    networks,
    transports: {
      [genlayerLocalnet.id]: makeStrictJsonRpcTransport(
        getDefaultRpcUrl(genlayerLocalnet),
      ),
      [genlayerBradbury.id]: makeStrictJsonRpcTransport(
        getDefaultRpcUrl(genlayerBradbury),
      ),
    },
    ssr: false,
  });

  wagmiAdapterRef.value = adapter;

  createAppKit({
    adapters: [adapter],
    projectId,
    networks,
    defaultNetwork: genlayerLocalnet,
    metadata: {
      name: 'GenLayer Studio',
      description: 'GenLayer Intelligent Contracts Sandbox',
      url: window.location.origin,
      icons: [],
    },
    features: {
      email: false,
      socials: false,
      swaps: false,
      onramp: false,
      send: false,
    },
    allowUnsupportedChain: true,
    themeMode: 'light',
  });

  appKitReady = true;
}
