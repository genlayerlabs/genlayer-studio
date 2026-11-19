import UnionField from '@/components/global/fields/UnionField.vue';
import { getUnionTypes } from '@/components/global/fields/fieldValues';
import AnyField from '@/components/global/fields/AnyField.vue';
import StringField from '@/components/global/fields/StringField.vue';
import IntegerField from '@/components/global/fields/IntegerField.vue';
import BooleanField from '@/components/global/fields/BooleanField.vue';
import type { ContractParamsSchema } from 'genlayer-js/types';

export const InputTypesMap: { [k: string]: any } = {
  string: StringField,
  int: IntegerField,
  bool: BooleanField,
  any: AnyField,
};

export const useInputMap = () => {
  const getComponent = (type: ContractParamsSchema) => {
    if (getUnionTypes(type).length) return UnionField;
    if (typeof type !== 'string') {
      type = 'any';
    }
    const component = InputTypesMap[type];

    if (!component) {
      console.warn(
        `Component not found for input type: ${type}, defaulting to any`,
      );
      return AnyField;
    }

    return component;
  };

  return { getComponent };
};
