<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import FieldLabel from './FieldLabel.vue';
import { parseIntegerInput } from './integerInput';
import { useUniqueId } from '@/hooks';

const fieldId = useUniqueId('number');

defineProps<{
  name: string;
  label: string;
  testId?: string;
  placeholder?: string;
}>();

const model = defineModel<unknown>();

// This is a text input and not `<input type="number">`: Vue turns the value of a
// number input into a JS number, which rounds every integer above 2^53 before it
// can reach the contract. The text stays what the user typed and the value that
// is emitted is an exact `bigint` (see `parseIntegerInput`)
const toText = (value: unknown) =>
  value === undefined || value === null ? '' : String(value);

const text = ref(toText(model.value));
let lastEmitted: unknown = model.value;

// follow the value when it is changed from outside, for example reset to the
// default, but do not rewrite what is being typed
watch(model, (value) => {
  if (Object.is(value, lastEmitted)) {
    return;
  }
  lastEmitted = value;
  text.value = toText(value);
});

const invalid = computed(() => Number.isNaN(parseIntegerInput(text.value)));

function onInput(event: Event) {
  text.value = (event.target as HTMLInputElement).value;
  lastEmitted = parseIntegerInput(text.value);
  model.value = lastEmitted;
}
</script>

<template>
  <div class="flex w-full flex-col">
    <FieldLabel :for="fieldId" tiny>{{ name }}</FieldLabel>
    <input
      :id="fieldId"
      type="text"
      :name="name"
      :value="text"
      :placeholder="placeholder"
      :data-testid="testId"
      autocomplete="off"
      spellcheck="false"
      lang="en"
      class="input-style px-2 py-1"
      :class="
        invalid
          ? 'ring-red-600/80 hover:ring-red-600'
          : 'ring-gray-400/60 hover:ring-gray-400 dark:ring-zinc-500/60 dark:hover:ring-zinc-500'
      "
      @input="onInput"
    />
  </div>
</template>
