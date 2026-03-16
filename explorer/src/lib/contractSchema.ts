import { getClient } from './genlayerClient';
import type { Address } from 'viem';
import type { CalldataEncodable, TransactionHashVariant } from 'genlayer-js/types';

// ---------------------------------------------------------------------------
// Types — re-export SDK types with simpler aliases
// ---------------------------------------------------------------------------

export type ContractParam = [name: string, type: string];

export interface ContractMethod {
  readonly: boolean;
  params: ContractParam[];
  kwparams: Record<string, string>;
  ret: string;
  payable?: boolean;
}

export interface ContractSchema {
  ctor: { params: ContractParam[]; kwparams: Record<string, string> };
  methods: Record<string, ContractMethod>;
}

// ---------------------------------------------------------------------------
// Schema fetch (via SDK)
// ---------------------------------------------------------------------------

export async function fetchContractSchema(address: string): Promise<ContractSchema> {
  const client = getClient();
  const result = await client.getContractSchema(address as Address);
  return result as unknown as ContractSchema;
}

// ---------------------------------------------------------------------------
// Read call (via SDK)
// ---------------------------------------------------------------------------

export async function callReadMethod(
  address: string,
  methodName: string,
  args: unknown[],
): Promise<unknown> {
  const client = getClient();
  const result = await client.readContract({
    address: address as Address,
    functionName: methodName,
    args: args as CalldataEncodable[],
    transactionHashVariant: 'latest-nonfinal' as TransactionHashVariant,
  });
  return result;
}

// ---------------------------------------------------------------------------
// Param parsing
// ---------------------------------------------------------------------------

export function parseParamValue(value: string, type: string): unknown {
  if (type === 'int') {
    const n = Number(value);
    return Number.isSafeInteger(n) ? n : BigInt(value);
  }
  if (type === 'bool') return value === 'true';
  if (type === 'string' || type === 'str') return value;
  // "any" or complex types — try JSON parse
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}
