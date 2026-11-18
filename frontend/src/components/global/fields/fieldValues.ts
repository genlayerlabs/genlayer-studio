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
      return null;
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
