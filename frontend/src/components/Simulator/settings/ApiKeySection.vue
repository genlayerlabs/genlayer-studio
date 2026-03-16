<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { notify } from '@kyvg/vue3-notification';
import PageSection from '@/components/Simulator/PageSection.vue';
import { KeyIcon } from 'lucide-vue-next';

const API_KEY_STORAGE_KEY = 'settingsStore.apiKey';

const apiKey = ref('');
const isMasked = ref(true);

onMounted(() => {
  apiKey.value = localStorage.getItem(API_KEY_STORAGE_KEY) || '';
});

function saveApiKey() {
  localStorage.setItem(API_KEY_STORAGE_KEY, apiKey.value.trim());
  notify({
    title: 'API key saved',
    type: 'success',
  });
}

function clearApiKey() {
  apiKey.value = '';
  localStorage.removeItem(API_KEY_STORAGE_KEY);
  notify({
    title: 'API key removed',
    type: 'success',
  });
}
</script>

<template>
  <PageSection>
    <template #title>API Key</template>

    <p class="text-muted-foreground mb-3 text-sm">
      Enter your API key to use authenticated rate limits. Without a key,
      anonymous rate limits apply.
    </p>

    <div class="flex items-center gap-2">
      <input
        v-model="apiKey"
        :type="isMasked ? 'password' : 'text'"
        placeholder="glk_..."
        class="border-border bg-background focus:ring-ring flex-1 rounded border px-3 py-2 font-mono text-sm focus:outline-none focus:ring-1"
        data-testid="input-api-key"
      />
      <Btn
        @click="isMasked = !isMasked"
        secondary
        size="sm"
        data-testid="btn-toggle-api-key-visibility"
      >
        {{ isMasked ? 'Show' : 'Hide' }}
      </Btn>
    </div>

    <div class="mt-3 flex gap-2">
      <Btn
        @click="saveApiKey"
        :icon="KeyIcon"
        size="sm"
        data-testid="btn-save-api-key"
      >
        Save
      </Btn>
      <Btn
        v-if="apiKey"
        @click="clearApiKey"
        secondary
        size="sm"
        data-testid="btn-clear-api-key"
      >
        Clear
      </Btn>
    </div>
  </PageSection>
</template>
