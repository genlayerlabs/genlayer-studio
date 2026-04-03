<script setup lang="ts">
import { useAccountsStore } from '@/stores';
import AccountItem from '@/components/Simulator/AccountItem.vue';
import { Dropdown } from 'floating-vue';
import { Wallet, Droplets } from 'lucide-vue-next';
import { PlusIcon } from '@heroicons/vue/16/solid';
import { notify } from '@kyvg/vue3-notification';
import { useEventTracking, useWallet, useRpcClient } from '@/hooks';
import { computed, ref } from 'vue';

const store = useAccountsStore();
const { trackEvent } = useEventTracking();
const { connect, disconnect } = useWallet();
const rpcClient = useRpcClient();

const hasExternalAccount = computed(() =>
  store.accounts.some((account) => account.type === 'external'),
);

const showFaucet = ref(false);
const faucetAmount = ref('10');
const isFunding = ref(false);

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

const handleFundAccount = async () => {
  if (!store.selectedAccount?.address) return;
  const amount = parseFloat(faucetAmount.value);
  if (isNaN(amount) || amount <= 0) {
    notify({ title: 'Enter a valid amount', type: 'error' });
    return;
  }

  isFunding.value = true;
  try {
    const weiAmount = BigInt(Math.floor(amount * 1e18));
    await rpcClient.fundAccount(
      store.selectedAccount.address,
      Number(weiAmount),
    );
    notify({
      title: `Funded ${amount} GEN`,
      type: 'success',
    });
    showFaucet.value = false;
  } catch (e: any) {
    notify({
      title: 'Error funding account',
      text: e?.message,
      type: 'error',
    });
  } finally {
    isFunding.value = false;
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
    <Dropdown placement="bottom-end">
      <GhostBtn v-tooltip="'Switch account'">
        <Wallet class="h-5 w-5" />
        {{ store.displayAddress }}
      </GhostBtn>

      <template #popper>
        <div class="divide-y divide-gray-200 dark:divide-gray-800">
          <AccountItem
            v-for="account in store.accounts"
            :key="account.address"
            :account="account"
            :active="account.address === store.selectedAccount?.address"
            :canDelete="account.type === 'local'"
            v-close-popper
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

    <Dropdown placement="bottom-end" :shown="showFaucet">
      <button
        @click="showFaucet = !showFaucet"
        v-tooltip="'Fund account'"
        class="rounded p-1 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-zinc-700 dark:hover:text-gray-200"
      >
        <Droplets class="h-4 w-4" />
      </button>

      <template #popper>
        <div class="flex flex-col gap-2 p-3" style="min-width: 220px">
          <div class="text-xs font-medium">Fund Account</div>
          <div class="truncate font-mono text-[10px] text-gray-500">
            {{ store.selectedAccount?.address }}
          </div>
          <div class="flex flex-row items-center gap-1">
            <input
              v-model="faucetAmount"
              type="number"
              min="0"
              step="1"
              class="w-full rounded border border-gray-300 bg-white px-2 py-1 text-sm dark:border-gray-600 dark:bg-zinc-800"
              placeholder="Amount"
              @keyup.enter="handleFundAccount"
            />
            <span class="text-xs text-gray-500">GEN</span>
          </div>
          <Btn
            @click="handleFundAccount"
            class="w-full"
            :loading="isFunding"
            :disabled="isFunding"
          >
            Fund
          </Btn>
        </div>
      </template>
    </Dropdown>
  </div>
</template>
