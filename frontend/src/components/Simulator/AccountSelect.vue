<script setup lang="ts">
import { useAccountsStore, useNetworkStore } from '@/stores';
import AccountItem from '@/components/Simulator/AccountItem.vue';
import FundAccountPopover from './FundAccountPopover.vue';
import { Dropdown } from 'floating-vue';
import { Wallet, Droplets } from 'lucide-vue-next';
import { PlusIcon } from '@heroicons/vue/16/solid';
import { notify } from '@kyvg/vue3-notification';
import { useEventTracking, useWallet } from '@/hooks';
import { formatGenBalance } from '@/utils/tokenAmount';
import { computed, watch } from 'vue';

const store = useAccountsStore();
const networkStore = useNetworkStore();
const { trackEvent } = useEventTracking();
const { connect, disconnect } = useWallet();

const PUBLIC_TESTNET_FAUCET_URL = 'https://testnet-faucet.genlayer.foundation';

const hasExternalAccount = computed(() =>
  store.accounts.some((account) => account.type === 'external'),
);

const currentBalance = computed(() => {
  const account = store.selectedAccount;
  const balance = account && store.balances[account.address];
  return balance == null ? null : formatGenBalance(balance);
});

watch(
  [() => store.selectedAccount?.address, () => networkStore.currentNetwork],
  ([address]) => {
    if (address) void store.refreshBalance(address);
  },
  { immediate: true },
);

const refreshAccounts = () => {
  for (const { address } of store.accounts) void store.refreshBalance(address);
};

const openTestnetFaucet = () => {
  window.open(PUBLIC_TESTNET_FAUCET_URL, '_blank', 'noopener,noreferrer');
};

const handleCreateNewAccount = async () => {
  const address = store.generateNewAccount();

  if (address) {
    notify({
      title: 'New Account Created',
      type: 'success',
    });

    trackEvent('created_account');
  } else {
    notify({
      title: 'Error creating a new account',
      type: 'error',
    });
  }
};

const connectWallet = () => {
  connect();
};

const disconnectWallet = async () => {
  try {
    await disconnect();
  } catch {
    notify({
      title: 'Error disconnecting wallet',
      type: 'error',
    });
  }
};
</script>

<template>
  <div class="flex flex-row items-center gap-1">
    <Dropdown placement="bottom-end" @show="refreshAccounts">
      <GhostBtn v-tooltip="'Switch account'">
        <Wallet class="h-5 w-5" />
        {{ store.displayAddress }}
        <span
          v-if="currentBalance !== null"
          class="ml-1 text-xs text-gray-500 dark:text-gray-400"
        >
          {{ currentBalance }} GEN
        </span>
      </GhostBtn>

      <template #popper>
        <div class="divide-y divide-gray-200 dark:divide-gray-800">
          <AccountItem
            v-for="account in store.accounts"
            :key="account.address"
            :account="account"
            :active="account.address === store.selectedAccount?.address"
            :canDelete="account.type === 'local'"
          />
        </div>

        <div
          class="flex w-full flex-row gap-1 border-t border-gray-300 bg-gray-200 p-1 dark:border-gray-600 dark:bg-gray-800"
        >
          <Btn
            @click="handleCreateNewAccount"
            secondary
            class="w-full"
            :icon="PlusIcon"
          >
            New account
          </Btn>
          <Btn
            v-if="!hasExternalAccount"
            @click="connectWallet"
            secondary
            class="w-full"
          >
            Connect Wallet
          </Btn>
          <Btn
            v-if="hasExternalAccount"
            @click="disconnectWallet"
            secondary
            class="w-full"
          >
            Disconnect Wallet
          </Btn>
        </div>
      </template>
    </Dropdown>

    <FundAccountPopover
      v-if="networkStore.isStudio && store.selectedAccount?.type === 'local'"
      :key="store.selectedAccount.address"
      :account="store.selectedAccount"
    />

    <button
      v-else-if="!networkStore.isStudio"
      @click="openTestnetFaucet"
      v-tooltip="
        'Open the testnet faucet (100 GEN / 24h, requires 0.01 ETH on mainnet)'
      "
      class="rounded p-1 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-zinc-700 dark:hover:text-gray-200"
    >
      <Droplets class="h-4 w-4" />
    </button>
  </div>
</template>
