<script setup lang="ts">
import { computed, ref } from 'vue';
import { Dropdown } from 'floating-vue';
import { Droplets } from 'lucide-vue-next';
import { notify } from '@kyvg/vue3-notification';
import { useAccountsStore, type AccountInfo } from '@/stores';
import { formatGenBalance, parseGenAmountToWei } from '@/utils/tokenAmount';

const props = defineProps<{ account: AccountInfo; showLabel?: boolean }>();
const store = useAccountsStore();
const shown = ref(false);
const amount = ref('');
const error = ref('');
const balance = computed(() => store.balances[props.account.address]);
const isFunding = computed(() => store.funding[props.account.address]);
const presets = ['100', '1000', '10000'];

async function fund(value: string) {
  if (isFunding.value) return;
  const wei = parseGenAmountToWei(value);
  if (wei === null || wei <= 0n) {
    error.value = 'Enter a positive amount with up to 18 decimal places.';
    return;
  }

  error.value = '';
  try {
    await store.fundAccount(props.account, wei);
    notify({ title: `Funded ${value.trim()} GEN`, type: 'success' });
    amount.value = '';
    shown.value = false;
  } catch (err) {
    error.value =
      err instanceof Error ? err.message : 'Unable to fund account.';
  }
}

function refresh() {
  error.value = '';
  void store.refreshBalance(props.account.address);
}
</script>

<template>
  <Dropdown v-model:shown="shown" placement="bottom-end" @show="refresh">
    <button
      type="button"
      aria-label="Fund account"
      class="flex items-center gap-1 rounded p-1 text-xs text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-zinc-700"
    >
      <Droplets class="h-4 w-4" />
      <span v-if="showLabel">Fund</span>
    </button>

    <template #popper>
      <form
        data-account-funding
        class="flex w-64 flex-col gap-2 p-3"
        @submit.prevent="fund(amount)"
      >
        <div class="text-xs text-gray-500 dark:text-gray-400">
          Current:
          {{ balance === undefined ? '—' : formatGenBalance(balance) + ' GEN' }}
        </div>
        <div class="flex gap-1">
          <Btn
            v-for="preset in presets"
            :key="preset"
            type="button"
            secondary
            tiny
            :disabled="isFunding"
            @click="fund(preset)"
          >
            +{{ preset }}
          </Btn>
        </div>
        <label class="text-xs">
          Custom amount (GEN)
          <input
            v-model="amount"
            type="text"
            inputmode="decimal"
            :disabled="isFunding"
            class="mt-1 w-full rounded border border-gray-300 bg-white px-2 py-1 text-sm dark:border-gray-600 dark:bg-zinc-800"
          />
        </label>
        <p
          v-if="error"
          role="alert"
          class="text-xs text-red-600 dark:text-red-400"
        >
          {{ error }}
        </p>
        <Btn type="submit" :loading="isFunding" :disabled="isFunding">Fund</Btn>
      </form>
    </template>
  </Dropdown>
</template>
