import type {
  CalldataEncodable,
  ContractParamsSchema,
} from 'genlayer-js/types';
import { AnyFieldValue } from './AnyFieldValue';

export function getUnionTypes(
  schema: ContractParamsSchema,
): ContractParamsSchema[] {
  if (
    typeof schema === 'object' &&
    !Array.isArray(schema) &&
    '$or' in schema &&
    Array.isArray(schema.$or)
  ) {
    return schema.$or as ContractParamsSchema[];
  }
  return [];
}

export function getDefaultUnionIndex(types: ContractParamsSchema[]): number {
  const nullIndex = types.indexOf('null');
  return nullIndex < 0 ? 0 : nullIndex;
}

export function getDefaultValueForType(
  type: ContractParamsSchema,
): CalldataEncodable | AnyFieldValue {
  const types = getUnionTypes(type);
  if (types.length)
    return getDefaultValueForType(types[getDefaultUnionIndex(types)]!);
  switch (type) {
    case 'null':
      return new AnyFieldValue('null');
    case 'bool':
      return false;
    case 'string':
      return '';
    case 'int':
      return 0;
    default:
      return new AnyFieldValue('');
  }
}

export function getComponentProps(type: ContractParamsSchema) {
  return getUnionTypes(type).length ? { schema: type } : {};
}

export function getTypeLabel(type: ContractParamsSchema): string {
  if (typeof type === 'string') return type === 'null' ? 'None' : type;
  const union = getUnionTypes(type);
  if (union.length) return union.map(getTypeLabel).join(' | ');
  if (Array.isArray(type)) {
    const element = type[0];
    if (
      type.length === 1 &&
      typeof element === 'object' &&
      !Array.isArray(element) &&
      '$rep' in element
    ) {
      return typeof element.$rep === 'string'
        ? `list[${getTypeLabel(element.$rep)}]`
        : 'list';
    }
    return 'list';
  }
  if ('$rep' in type) {
    return typeof type.$rep === 'string'
      ? `list[${getTypeLabel(type.$rep)}]`
      : 'list';
  }
  if ('$dict' in type) {
    return typeof type.$dict === 'string'
      ? `dict[${getTypeLabel(type.$dict)}]`
      : 'dict';
  }
  return 'struct';
}
