import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './test/metamask',
  testMatch: '*headless*',
  timeout: 30_000,
  retries: 0,
  workers: 1,
  use: {
    headless: true,
  },
});
