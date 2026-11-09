<script setup lang="ts">
import { useContractsStore } from '@/stores';
import { type ContractFile } from '@/types';
import {
  DocumentCheckIcon,
  DocumentPlusIcon,
  PencilSquareIcon,
  TrashIcon,
  ArrowDownOnSquareIcon,
} from '@heroicons/vue/16/solid';
import { ref, onMounted, nextTick } from 'vue';
import { notify } from '@kyvg/vue3-notification';

const store = useContractsStore();
const defaultContractName = 'New Contract.py';

const props = defineProps<{
  contract?: ContractFile;
  isActive?: boolean;
  isNewFile?: boolean;
}>();

const emit = defineEmits(['save', 'cancel']);

const isEditing = ref(false);
const editInput = ref<HTMLInputElement | null>(null);
const editingFileName = ref('');
const deleteModalOpen = ref(false);

const handleEditFile = () => {
  isEditing.value = true;

  if (props.isNewFile) {
    editingFileName.value = defaultContractName;
  } else if (props.contract) {
    editingFileName.value = props.contract.name;
  }

  const dotPosition = editingFileName.value.indexOf('.');

  nextTick(() => {
    editInput.value?.focus();
    editInput.value?.setSelectionRange(0, dotPosition);
  });
};

onMounted(() => {
  if (props.isNewFile) {
    handleEditFile();
  }
});

const handleStopEditing = () => {
  isEditing.value = false;
  editingFileName.value = '';
  editInput.value?.blur();
  emit('cancel');
};

const handleSaveFile = (e: Event) => {
  e.preventDefault();

  if (isEditing.value === false) {
    return; // Avoid double events when press Enter + blur
  }

  isEditing.value = false;

  if (props.isNewFile) {
    emit('save', editingFileName.value);
    return;
  } else if (props.contract) {
    store.updateContractFile(props.contract.id, {
      name: editingFileName.value,
    });
  }

  editingFileName.value = '';
  editInput.value?.blur();
};

const handleRemoveFile = (id: string) => {
  store.removeContractFile(id);
  deleteModalOpen.value = false;
  notify({
    type: 'success',
    title: 'Contract deleted',
  });
};

const handleDownloadFile = (e: Event) => {
  e.preventDefault();
  if (!props.contract?.content) return;
  const blob = new Blob([props.contract.content], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = String(props.contract?.name);
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
};
</script>

<template>
  <div class="flex w-full cursor-pointer flex-col">
    <div
      :class="[
        'group flex items-center px-2 py-1.5 text-xs transition-colors',
        isActive &&
          'bg-gray-200 hover:bg-gray-200 dark:bg-zinc-600 dark:text-white hover:dark:bg-zinc-600',
        !isActive &&
          'bg-white text-neutral-500 hover:bg-gray-100 dark:bg-zinc-800 hover:dark:bg-zinc-700',
      ]"
    >
      <DocumentPlusIcon
        v-if="isNewFile"
        class="mr-1 h-4 w-4 fill-primary dark:fill-white"
      />
      <DocumentCheckIcon
        v-else
        class="mr-1 h-4 w-4 fill-primary dark:fill-white"
      />

      <div class="w-full" v-if="isEditing">
        <input
          type="text"
          ref="editInput"
          class="!dark:ring-white !focus:outline -m-[1px] w-full !rounded-[1px] !border-none bg-slate-50 p-[1px] text-xs font-semibold !shadow-none !outline-none !ring-gray-400 focus:outline-gray-400 dark:bg-zinc-700 dark:text-gray-200"
          v-model="editingFileName"
          @blur="handleSaveFile"
          @keydown.enter="handleSaveFile"
          @keydown.escape="handleStopEditing"
        />
      </div>

      <div
        v-else-if="contract"
        class="flex w-full items-center justify-between truncate"
      >
        <div data-testid="contract-file" class="truncate font-semibold">
          {{ contract.name }}
        </div>

        <div class="hidden flex-row gap-1 group-hover:flex">
          <button @click.stop="handleEditFile" v-tooltip="'Edit Name'">
            <PencilSquareIcon
              class="h-[16px] w-[16px] p-[2px] text-gray-400 transition-all hover:text-gray-800 active:scale-90 dark:hover:text-white"
            />
          </button>

          <button @click.stop="handleDownloadFile" v-tooltip="'Download file'">
            <ArrowDownOnSquareIcon
              class="h-[16px] w-[16px] p-[2px] text-gray-400 transition-all hover:text-gray-800 active:scale-90 dark:hover:text-white"
            />
          </button>

          <button
            @click.stop="deleteModalOpen = true"
            v-tooltip="'Delete file'"
          >
            <TrashIcon
              class="h-[16px] w-[16px] p-[2px] text-gray-400 transition-all hover:text-gray-800 active:scale-90 dark:hover:text-white"
            />
          </button>
        </div>
      </div>

      <ConfirmationModal
        v-if="contract"
        :open="deleteModalOpen"
        @close="deleteModalOpen = false"
        @confirm="handleRemoveFile(contract.id)"
        buttonText="Delete Contract"
        dangerous
      >
        <template #title>Delete Contract</template>
        <template #description
          >Are you sure you want to delete this contract?</template
        >
        <template #info>{{ contract.name }}</template>
      </ConfirmationModal>
    </div>
  </div>
</template>
