import { defineWalletSetup } from '@synthetixio/synpress';
import { MetaMask } from '@synthetixio/synpress/playwright';

const WALLET_PASSWORD = process.env.E2E_METAMASK_PASSWORD ?? 'GenLayerE2E!123';

const ANVIL_DEFAULT_MNEMONIC =
  'test test test test test test test test test test test junk';

const WALLET_SEED_PHRASE =
  process.env.E2E_METAMASK_SEED_PHRASE ?? ANVIL_DEFAULT_MNEMONIC;

// prettier-ignore
export default defineWalletSetup(
  WALLET_PASSWORD,
  async (context, metamaskPage) => {
    const metamask = new MetaMask(context, metamaskPage, WALLET_PASSWORD);
    await metamask.importWallet(WALLET_SEED_PHRASE);
  }
);
