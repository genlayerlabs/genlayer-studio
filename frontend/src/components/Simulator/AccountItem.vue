<script setup lang="ts">
import { useAccountsStore, useNetworkStore, type AccountInfo } from '@/stores';
import { notify } from '@kyvg/vue3-notification';
import { PowerCircle, Wallet } from 'lucide-vue-next';
import { computed, ref, watch } from 'vue';
import CopyTextButton from '../global/CopyTextButton.vue';
import { TrashIcon, CheckCircleIcon } from '@heroicons/vue/16/solid';
import { formatGenBalance } from '@/utils/tokenAmount';
import FundAccountPopover from './FundAccountPopover.vue';
const store = useAccountsStore();
const networkStore = useNetworkStore();
const props = defineProps<{
  active?: boolean;
  account: AccountInfo;
  canDelete?: boolean;
}>();

const balance = computed(() => store.balances[props.account.address]);
const highlighted = ref(false);
watch(balance, (value, previous, onCleanup) => {
  highlighted.value =
    value !== undefined && previous !== undefined && value > previous;
  if (!highlighted.value) return;
  const timer = setTimeout(() => {
    highlighted.value = false;
  }, 1200);
  onCleanup(() => clearTimeout(timer));
});
watch(
  [() => props.account.address, () => networkStore.currentNetwork],
  ([address]) => void store.refreshBalance(address),
  { immediate: true },
);

const setCurrentAddress = () => {
  store.setCurrentAccount(props.account);
  notify({
    title: 'Active account changed',
    type: 'success',
  });
};

const deleteAddress = () => {
  try {
    store.removeAccount(props.account);
    notify({
      title: 'Account deleted',
      type: 'success',
    });
  } catch (err) {
    console.error(err);
    notify({
      title: 'Error',
      text: (err as Error)?.message || 'Error deleting account',
      type: 'error',
    });
  }
};

const showConfirmDelete = ref(false);
</script>

<template>
  <div
    class="group flex items-center gap-2 bg-slate-100 p-2 hover:bg-slate-200 dark:bg-gray-700 dark:hover:bg-gray-600"
  >
    <button
      type="button"
      v-close-popper
      class="flex min-w-0 grow items-center gap-2 text-left"
      @click="setCurrentAddress"
    >
      <div>
        <PowerCircle
          v-if="!active"
          class="h-4 w-4 text-gray-400 opacity-0 transition-all group-hover:opacity-100"
          v-tooltip="'Activate Account'"
        />
        <PowerCircle class="h-4 w-4 text-green-500" v-if="active" />
      </div>

      <div
        class="flex grow flex-col truncate"
        :class="[!active && 'opacity-50']"
      >
        <span class="truncate font-mono text-xs font-semibold">
          {{ account.address }}
        </span>
        <span
          v-if="balance !== undefined"
          class="w-fit rounded font-mono text-[10px] text-gray-500 transition-colors dark:text-gray-400"
          :class="{ 'bg-green-200 dark:bg-green-900': highlighted }"
        >
          {{ formatGenBalance(balance) }} GEN
        </span>
      </div>

      <Wallet
        v-if="account.type === 'external'"
        class="h-4 w-4 text-blue-500"
        v-tooltip="'External Wallet'"
      />
    </button>

    <div class="flex shrink-0 items-center gap-1">
      <CopyTextButton :text="account.address" v-tooltip="'Copy Address'" />

      <FundAccountPopover
        v-if="networkStore.isStudio && account.type === 'local'"
        :account="account"
        show-label
      />

      <Transition mode="out-in" v-if="canDelete">
        <button
          v-if="!showConfirmDelete"
          data-testid="account-item-delete"
          @click.stop="showConfirmDelete = true"
          v-tooltip="'Delete Account'"
        >
          <TrashIcon
            class="h-5 w-5 p-[2px] text-slate-400 transition-colors hover:text-slate-800 active:scale-90 dark:hover:text-white"
          />
        </button>

        <button
          v-else
          data-testid="account-item-confirm-delete"
          @click.stop="deleteAddress"
          v-tooltip="'Confirm deletion'"
        >
          <CheckCircleIcon
            class="h-5 w-5 p-[2px] text-red-500 transition-colors hover:text-red-400 active:scale-90"
          />
        </button>
      </Transition>
    </div>
  </div>
</template>

<style scoped>
.v-enter-active,
.v-leave-active {
  transition: all 0.08s ease;
}
.v-enter-from {
  opacity: 0;
  transform: scale(0.8) rotate(45deg);
}
.v-leave-to {
  opacity: 0;
  transform: scale(0.8) rotate(-45deg);
}
</style>
