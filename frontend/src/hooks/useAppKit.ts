import { shallowRef } from 'vue';
import { WagmiAdapter } from '@reown/appkit-adapter-wagmi';
import { createAppKit } from '@reown/appkit/vue';
import type { AppKitNetwork } from '@reown/appkit/networks';
import { mainnet, sepolia } from '@reown/appkit/networks';
import { getRuntimeConfig } from '@/utils/runtimeConfig';
import { createGenlayerLocalnet } from './useNetworks';

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

  const networks: [AppKitNetwork, ...AppKitNetwork[]] = [
    genlayerLocalnet,
    mainnet,
    sepolia,
  ];

  const adapter = new GenlayerWagmiAdapter({
    projectId,
    networks,
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
