<script setup lang="ts">
import { useAccountsStore } from '@/stores';
import AccountItem from '@/components/Simulator/AccountItem.vue';
import { Dropdown } from 'floating-vue';
import { Wallet } from 'lucide-vue-next';
import { PlusIcon } from '@heroicons/vue/16/solid';
import { notify } from '@kyvg/vue3-notification';
import { useEventTracking, useWallet } from '@/hooks';
import { computed } from 'vue';

const store = useAccountsStore();
const { trackEvent } = useEventTracking();
const { connect } = useWallet();

const hasExternalAccount = computed(() =>
  store.accounts.some((account) => account.type === 'external'),
);

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
</script>

<template>
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
      </div>
    </template>
  </Dropdown>
</template>
