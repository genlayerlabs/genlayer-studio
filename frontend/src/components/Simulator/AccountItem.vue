<script setup lang="ts">
import { useAccountsStore, type AccountInfo } from '@/stores';
import { notify } from '@kyvg/vue3-notification';
import { PowerCircle, Wallet } from 'lucide-vue-next';
import { ref } from 'vue';
import CopyTextButton from '../global/CopyTextButton.vue';
import { TrashIcon, CheckCircleIcon } from '@heroicons/vue/16/solid';
const store = useAccountsStore();

const setCurentAddress = () => {
  store.setCurrentAccount(props.account as AccountInfo);
  notify({
    title: 'Active account changed',
    type: 'success',
  });
};

const deleteAddress = () => {
  try {
    store.removeAccount(props.account as AccountInfo);
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

const props = defineProps<{
  active?: boolean;
  account: AccountInfo;
  canDelete?: boolean;
}>();

const showConfirmDelete = ref(false);
</script>

<template>
  <div>
    <button
      class="group flex w-full flex-row items-center justify-between gap-2 truncate bg-slate-100 p-2 text-left hover:bg-slate-200 dark:bg-gray-700 dark:hover:bg-gray-600"
      @click="setCurentAddress()"
    >
      <div>
        <PowerCircle
          v-if="!active"
          class="h-4 w-4 text-gray-400 opacity-0 transition-all group-hover:opacity-100"
          v-tooltip="'Activate Account'"
        />
        <PowerCircle class="h-4 w-4 text-green-500" v-if="active" />
      </div>

      <span
        class="flex grow flex-row truncate font-mono text-xs font-semibold"
        :class="[!active && 'opacity-50']"
      >
        {{ account.address }}
      </span>

      <Wallet
        v-if="account.type === 'metamask'"
        class="h-4 w-4 text-orange-500"
        v-tooltip="'MetaMask Account'"
      />

      <div
        class="flex flex-row items-center gap-1 opacity-0 group-hover:opacity-100"
      >
        <CopyTextButton :text="account.address" v-tooltip="'Copy Address'" />

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
    </button>
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
