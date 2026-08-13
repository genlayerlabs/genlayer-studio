import { beforeEach, describe, expect, it, vi } from 'vitest';
import { recordWalletConnection } from '@/services/walletAnalytics';

describe('wallet analytics service', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response('{}', { status: 200 }))),
    );
    localStorage.clear();
    vi.unstubAllEnvs();
  });

  it('posts wallet address to the analytics endpoint derived from RPC URL', async () => {
    vi.stubEnv('VITE_JSON_RPC_SERVER_URL', 'https://studio.example.com/api');

    await recordWalletConnection('0x1111111111111111111111111111111111111111');

    expect(fetch).toHaveBeenCalledWith(
      'https://studio.example.com/api/analytics/wallet-connections',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          wallet_address: '0x1111111111111111111111111111111111111111',
        }),
      }),
    );
  });

  it('sends the configured API key when present', async () => {
    localStorage.setItem('settingsStore.apiKey', 'glk_test');

    await recordWalletConnection('0x1111111111111111111111111111111111111111');

    expect(fetch).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({
        headers: expect.objectContaining({ 'X-API-Key': 'glk_test' }),
      }),
    );
  });
});
