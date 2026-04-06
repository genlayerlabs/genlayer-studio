import { expect } from '@playwright/test';
import { testWithSynpress } from '@synthetixio/synpress';
import {
  type MetaMask,
  metaMaskFixtures,
} from '@synthetixio/synpress/playwright';
import walletSetup from './wallet.setup';

const test = testWithSynpress(metaMaskFixtures(walletSetup));

const RPC_URL = process.env.GENLAYER_RPC_URL ?? 'http://localhost:4000/api';
const CHAIN_ID = Number(process.env.GENLAYER_CHAIN_ID ?? '61127');

/**
 * Directly call eth_getBalance via JSON-RPC and return the wei balance as bigint.
 */
async function rpcGetBalance(address: string): Promise<bigint> {
  const res = await fetch(RPC_URL, {
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
  return BigInt(json.result);
}

/**
 * Fund an account via sim_fundAccount and return the tx hash.
 */
async function rpcFundAccount(
  address: string,
  amountWei: bigint,
): Promise<string> {
  const res = await fetch(RPC_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      jsonrpc: '2.0',
      method: 'sim_fundAccount',
      params: { account_address: address, amount: Number(amountWei) },
      id: 1,
    }),
  });
  const json = await res.json();
  if (json.error) throw new Error(json.error.message);
  return json.result;
}

test.describe('MetaMask balance visibility', () => {
  // Anvil account 0 (from default mnemonic)
  const ACCOUNT_ADDRESS = '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266';
  const TEN_GEN = BigInt('10000000000000000000'); // 10 * 1e18

  test('eth_getBalance returns correct value regardless of address casing', async () => {
    // Fund the account first
    await rpcFundAccount(ACCOUNT_ADDRESS, TEN_GEN);

    // Query with checksummed address
    const balanceChecksummed = await rpcGetBalance(ACCOUNT_ADDRESS);
    expect(balanceChecksummed).toBeGreaterThan(0n);

    // Query with lowercase address (how MetaMask sometimes sends it)
    const balanceLowercase = await rpcGetBalance(ACCOUNT_ADDRESS.toLowerCase());
    expect(balanceLowercase).toBe(balanceChecksummed);
  });

  test('MetaMask shows non-zero balance after funding via RPC', async ({
    context,
    metamask,
  }) => {
    // Add GenLayer as custom network
    await metamask.addNetwork({
      name: 'GenLayer Simulator',
      rpcUrl: RPC_URL,
      chainId: CHAIN_ID,
      symbol: 'GEN',
    });

    // Fund the default Anvil account
    await rpcFundAccount(ACCOUNT_ADDRESS, TEN_GEN);

    // Verify via direct RPC that balance is non-zero
    const balance = await rpcGetBalance(ACCOUNT_ADDRESS);
    expect(balance).toBeGreaterThan(0n);

    // Open a page and navigate to check MetaMask can query the balance
    const page = await context.newPage();

    // Inject a script that queries eth_getBalance via the provider
    const metamaskBalance = await page.evaluate(async (expectedAddress) => {
      // Access the injected ethereum provider
      const provider = (window as any).ethereum;
      if (!provider) return null;

      const result = await provider.request({
        method: 'eth_getBalance',
        params: [expectedAddress, 'latest'],
      });
      return result;
    }, ACCOUNT_ADDRESS);

    // MetaMask should return a hex balance > 0x0
    expect(metamaskBalance).not.toBeNull();
    expect(metamaskBalance).not.toBe('0x0');
    expect(BigInt(metamaskBalance as string)).toBeGreaterThan(0n);
  });
});
