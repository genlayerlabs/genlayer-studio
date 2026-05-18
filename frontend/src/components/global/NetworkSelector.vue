<script setup lang="ts">
import { Dropdown } from 'floating-vue';
import { CheckIcon } from '@heroicons/vue/16/solid';
import { Network, AlertTriangle } from 'lucide-vue-next';
import { notify } from '@kyvg/vue3-notification';
import {
  useNetworkStore,
  useTransactionsStore,
  type NetworkName,
} from '@/stores';
import { useChainEnforcer } from '@/hooks';
import { ref, computed } from 'vue';

const networkStore = useNetworkStore();
const transactionsStore = useTransactionsStore();
const { ensureCorrectChain, isExternalWallet } = useChainEnforcer();

const isSwitching = ref(false);

const active = computed(() =>
  networkStore.availableNetworks.find(
    (n) => n.name === networkStore.currentNetwork,
  ),
);

const pendingOnCurrentNetwork = computed(
  () =>
    transactionsStore.transactions.filter(
      (t) => t.statusName !== 'FINALIZED' && t.statusName !== 'CANCELED',
    ).length,
);

async function switchNetwork(name: NetworkName) {
  if (name === networkStore.currentNetwork || isSwitching.value) return;

  const previous = networkStore.currentNetwork;
  isSwitching.value = true;
  try {
    networkStore.setCurrentNetwork(name);

    if (isExternalWallet.value) {
      await ensureCorrectChain();
    }

    notify({
      title: `Switched to ${networkStore.chainName}`,
      type: 'success',
    });
  } catch (err: any) {
    // Wallet rejected or some other failure — revert.
    networkStore.setCurrentNetwork(previous);
    notify({
      title: 'Network switch canceled',
      text:
        err?.message ??
        'The wallet remained on the previous network. Try again or switch manually.',
      type: 'error',
    });
  } finally {
    isSwitching.value = false;
  }
}
</script>

<template>
  <Dropdown v-if="!networkStore.isLocked" placement="bottom-end">
    <GhostBtn v-tooltip="'Switch network'">
      <Network class="h-4 w-4" />
      <span class="text-sm">{{ active?.label ?? networkStore.chainName }}</span>
      <AlertTriangle
        v-if="!networkStore.isStudio"
        class="h-3.5 w-3.5 text-amber-500"
        v-tooltip="
          'Testnet — Studio features (logs, validators, faucet) are disabled.'
        "
      />
    </GhostBtn>

    <template #popper>
      <div class="min-w-[260px] p-1">
        <div class="px-2 py-1 text-xs uppercase text-gray-500">
          Connect studio to
        </div>
        <button
          v-for="n in networkStore.availableNetworks"
          :key="n.name"
          class="group flex w-full items-center justify-between rounded px-2 py-2 text-left text-sm hover:bg-gray-100 disabled:opacity-50 dark:hover:bg-zinc-700"
          :disabled="isSwitching"
          @click="switchNetwork(n.name)"
          v-close-popper
        >
          <div class="flex flex-col">
            <span class="font-medium">{{ n.label }}</span>
            <span class="font-mono text-[10px] text-gray-500">
              chain {{ n.chainId }} · {{ n.isStudio ? 'local' : 'testnet' }}
            </span>
          </div>
          <CheckIcon
            v-if="n.name === networkStore.currentNetwork"
            class="h-4 w-4 text-green-500"
          />
        </button>
        <div
          v-if="!networkStore.isStudio && pendingOnCurrentNetwork > 0"
          class="mx-1 mt-1 border-t border-gray-200 px-2 pt-2 text-[11px] text-amber-600 dark:border-gray-700"
        >
          {{ pendingOnCurrentNetwork }} pending transaction(s) on the current
          network will be hidden after switching.
        </div>
      </div>
    </template>
  </Dropdown>
</template>
