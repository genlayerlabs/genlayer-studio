import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './test/metamask',
  timeout: 120_000,
  retries: 0,
  workers: 1,
  fullyParallel: false,
  use: {
    headless: false, // Synpress requires headed mode for MetaMask extension
    baseURL: process.env.GENLAYER_RPC_URL ?? 'http://localhost:4000',
  },
});
