/**
 * Headless test proving MetaMask-compatible balance visibility.
 *
 * Verifies the full eth_getBalance flow:
 * 1. Fund account via sim_fundAccount
 * 2. Query balance with checksummed address
 * 3. Query balance with lowercase address (how MetaMask sends it)
 * 4. Query balance from a browser context (same path MetaMask takes)
 */
import { test, expect } from '@playwright/test';

const RPC_URL = process.env.GENLAYER_RPC_URL ?? 'http://localhost:4000/api';

// Anvil account 0
const ADDRESS_CHECKSUMMED = '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266';
const ADDRESS_LOWERCASE = ADDRESS_CHECKSUMMED.toLowerCase();
const TEN_GEN_WEI = 10_000_000_000_000_000_000; // 10 * 1e18

async function rpcCall(method: string, params: unknown = []) {
  const res = await fetch(RPC_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', method, params, id: 1 }),
  });
  const json = await res.json();
  if (json.error) throw new Error(`${method}: ${json.error.message}`);
  return json.result;
}

test.describe('eth_getBalance MetaMask compatibility', () => {
  test.beforeAll(async () => {
    // Fund account once
    await rpcCall('sim_fundAccount', {
      account_address: ADDRESS_CHECKSUMMED,
      amount: TEN_GEN_WEI,
    });
  });

  test('server: checksummed and lowercase return same non-zero balance', async () => {
    const balChecksummed = await rpcCall('eth_getBalance', [
      ADDRESS_CHECKSUMMED,
      'latest',
    ]);
    const balLowercase = await rpcCall('eth_getBalance', [
      ADDRESS_LOWERCASE,
      'latest',
    ]);

    expect(BigInt(balChecksummed)).toBeGreaterThan(0n);
    expect(balChecksummed).toBe(balLowercase);
  });

  test('browser: fetch-based eth_getBalance returns non-zero for both casings', async ({
    page,
  }) => {
    await page.goto('about:blank');

    // This is exactly what MetaMask's provider does under the hood:
    // fetch POST to the RPC URL with eth_getBalance
    const result = await page.evaluate(
      async ({ rpcUrl, checksummed, lowercase }) => {
        async function getBalance(address: string): Promise<string> {
          const res = await fetch(rpcUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              jsonrpc: '2.0',
              method: 'eth_getBalance',
              params: [address, 'latest'],
              id: 1,
            }),
          });
          const json = await res.json();
          return json.result;
        }

        return {
          checksummed: await getBalance(checksummed),
          lowercase: await getBalance(lowercase),
        };
      },
      {
        rpcUrl: RPC_URL,
        checksummed: ADDRESS_CHECKSUMMED,
        lowercase: ADDRESS_LOWERCASE,
      },
    );

    expect(BigInt(result.checksummed)).toBeGreaterThan(0n);
    expect(result.checksummed).toBe(result.lowercase);
  });
});
