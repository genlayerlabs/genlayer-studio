import { getApiKeyHeaders } from '@/utils/apiKey';
import { getRuntimeConfig } from '@/utils/runtimeConfig';
import type { Address } from '@/types';

const DEFAULT_RPC_URL = 'http://127.0.0.1:4000/api';

export async function recordWalletConnection(
  walletAddress: Address,
): Promise<void> {
  const response = await fetch(walletConnectionAnalyticsUrl(), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getApiKeyHeaders(),
    },
    body: JSON.stringify({ wallet_address: walletAddress }),
  });

  if (!response.ok) {
    throw new Error(`Wallet analytics request failed: ${response.status}`);
  }
}

function walletConnectionAnalyticsUrl(): string {
  const rpcUrl = getRuntimeConfig('VITE_JSON_RPC_SERVER_URL', DEFAULT_RPC_URL);
  return `${rpcUrl.replace(/\/$/, '')}/analytics/wallet-connections`;
}
