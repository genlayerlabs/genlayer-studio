<script setup lang="ts">
import { ref, watch, computed } from 'vue';
import { useUniqueId } from '@/hooks';
import TextInput from '../inputs/TextInput.vue';
import NumberInput from '../inputs/NumberInput.vue';
import CheckboxInput from '../inputs/CheckboxInput.vue';
import FieldLabel from './FieldLabel.vue';
import { AnyFieldValue } from './AnyFieldValue';

const props = defineProps<{
  name: string;
  unionTypes: string[];
  modelValue?: any;
}>();

const emit = defineEmits<{
  'update:modelValue': [value: any];
}>();

const fieldId = useUniqueId('union');
const selectedType = ref<string>('');
const selectedGroup = ref<string>('');
const values = ref<{ [key: string]: any }>({});
const isInternalUpdate = ref<boolean>(false);

const getInputComponent = (type: string) => {
  switch (type) {
    case 'int':
      return NumberInput;
    case 'bool':
      return CheckboxInput;
    default:
      return TextInput;
  }
};

const displayGroups = computed(() => {
  const groups: {
    id: string;
    types: string[];
    label: string;
    placeholder: string;
  }[] = [];
  const processed = new Set<string>();

  props.unionTypes.forEach((type) => {
    const trimmedType = type.trim();
    if (processed.has(trimmedType)) return;

    const component = getInputComponent(trimmedType);

    if (
      component === TextInput &&
      ['array', 'dict', 'address', 'bytes', 'any'].includes(trimmedType)
    ) {
      const complexTypes = props.unionTypes.filter((t) => {
        const tt = t.trim();
        return ['array', 'dict', 'address', 'bytes', 'any'].includes(tt);
      });

      if (complexTypes.length > 0) {
        const typeLabels = complexTypes.map((t) => t.trim().toLowerCase());
        groups.push({
          id: 'complex',
          types: complexTypes.map((t) => t.trim()),
          label: typeLabels.join(','),
          placeholder: typeLabels.join('/'),
        });

        complexTypes.forEach((t) => processed.add(t.trim()));
      }
    } else if (!processed.has(trimmedType)) {
      groups.push({
        id: trimmedType,
        types: [trimmedType],
        label: trimmedType,
        placeholder: trimmedType.toLowerCase(),
      });
      processed.add(trimmedType);
    }
  });

  return groups;
});

const initializeValues = () => {
  if (!props.unionTypes || props.unionTypes.length === 0) {
    return;
  }
  const typeMap: { [key: string]: any } = {
    string: '',
    int: 0,
    bool: false,
    address: '',
    bytes: '',
    array: '',
    dict: '',
    None: null,
    null: null,
    any: '',
  };

  props.unionTypes.forEach((type) => {
    const trimmedType = type.trim();
    if (!trimmedType) return;
    // Only initialize if the value doesn't already exist (preserve user input)
    if (!(trimmedType in values.value)) {
      values.value[trimmedType] = typeMap[trimmedType] ?? '';
    }
  });

  const groups = displayGroups.value;
  if (groups.length > 0) {
    selectedGroup.value = groups[0].id;
    selectedType.value = groups[0].types[0];
  } else {
    console.warn('No valid union type groups found');
  }
};

// Handle external model value updates
watch(
  () => props.modelValue,
  (newValue) => {
    // Skip if this update is from our own emitValue
    if (isInternalUpdate.value) {
      isInternalUpdate.value = false;
      return;
    }

    // Skip if the new value matches what we already have for the current type
    // This prevents overwriting user input when switching radio buttons
    if (newValue !== undefined && newValue instanceof AnyFieldValue) {
      const currentValue = values.value[selectedType.value];
      if (typeof currentValue === 'string' && currentValue === newValue.value) {
        return;
      }
    }

    if (newValue !== undefined && newValue instanceof AnyFieldValue) {
      // For strings, unwrap the JSON to get the original value
      if (typeof newValue.value === 'string') {
        try {
          values.value[selectedType.value] = JSON.parse(newValue.value);
        } catch {
          values.value[selectedType.value] = newValue.value;
        }
      } else {
        values.value[selectedType.value] = newValue.value;
      }
    }
  },
);

const getCurrentValue = () => {
  const type = selectedType.value;
  const value = values.value[type];

  if (!type || value === undefined) {
    return undefined;
  }

  if (type === 'string') {
    return new AnyFieldValue(JSON.stringify(value));
  }

  if (
    type === 'array' ||
    type === 'dict' ||
    type === 'address' ||
    type === 'bytes' ||
    type === 'any'
  ) {
    return new AnyFieldValue(value);
  }

  return value;
};

const emitValue = () => {
  const currentValue = getCurrentValue();
  isInternalUpdate.value = true;
  emit('update:modelValue', currentValue);
};

// Initialize and watch for changes
watch(
  () => props.unionTypes,
  (newTypes) => {
    if (newTypes?.length > 0) {
      initializeValues();
      emitValue();
    }
  },
  { immediate: true },
);

// Watch for group changes to update selected type
watch(selectedGroup, (newGroup) => {
  const groups = displayGroups.value;
  const group = groups.find((g) => g.id === newGroup);
  if (group && group.types.length > 0) {
    selectedType.value = group.types[0];
    emitValue();
  }
});

// Watch for value changes
watch([selectedType, values], () => {
  emitValue();
});
</script>

<template>
  <div class="flex w-full flex-col gap-2">
    <FieldLabel :for="fieldId" tiny>{{ name }}</FieldLabel>

    <div class="flex flex-col gap-2">
      <div
        v-for="group in displayGroups"
        :key="group.id"
        class="flex w-full flex-row items-center gap-2"
      >
        <input
          :id="`${fieldId}-radio-${group.id}`"
          v-model="selectedGroup"
          :value="group.id"
          type="radio"
          :name="`${fieldId}-group`"
          class="text-primary-600 h-4 w-4 border-gray-300 text-primary outline-0 focus:ring-accent dark:border-gray-500 dark:bg-transparent dark:text-accent"
        />

        <div
          v-if="group.id === 'None' || group.id === 'null'"
          class="text-xs text-gray-500"
        >
          None
        </div>

        <div v-else class="flex w-full flex-row items-center gap-2">
          <component
            :is="getInputComponent(group.types[0])"
            v-model="values[group.types[0]]"
            :placeholder="group.placeholder"
            :disabled="selectedGroup !== group.id"
            :id="`${fieldId}-${group.id}`"
            :name="`${props.name}-${group.id}`"
            @update:modelValue="emitValue"
            tiny
            :class="group.types[0] === 'bool' ? 'h-4 w-4' : 'w-full flex-1'"
          />
        </div>
      </div>
    </div>
  </div>
</template>
