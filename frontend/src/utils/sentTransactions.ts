import type { TransactionItem } from '@/types';
import {
  b64ToArray,
  calldataMethodName,
  calldataToUserFriendlyJson,
} from '@/calldata/jsonifier';

/**
 * Turns a lightweight `sim_getSentTransactions` row into a TransactionItem the
 * list/modal can render: calldata gets the `{ base64, readable }` shape the
 * Input section expects and the called method becomes the row label.
 */
export function toSentTransactionItem(
  tx: any,
  localContractId: string,
): TransactionItem {
  const isDeploy = tx.type === 1 || tx.type === 'deploy';
  const isUpgrade = tx.type === 3 || tx.type === 'upgrade';
  const data: Record<string, any> = { ...tx };
  let decodedData: TransactionItem['decodedData'];

  const rawCalldata = tx.data?.calldata;
  if (typeof rawCalldata === 'string' && rawCalldata) {
    const bytes = b64ToArray(rawCalldata);
    try {
      data.data = {
        ...tx.data,
        calldata: { base64: rawCalldata, ...calldataToUserFriendlyJson(bytes) },
      };
    } catch {
      // Undecodable: the Input section falls back to the base64 text.
      data.data = { ...tx.data, calldata: { base64: rawCalldata } };
    }
    const functionName = calldataMethodName(bytes);
    if (functionName) decodedData = { functionName, args: [], kwargs: {} };
  }

  return {
    hash: tx.hash,
    type: isDeploy ? 'deploy' : isUpgrade ? 'upgrade' : 'method',
    statusName: tx.statusName ?? tx.status_name ?? tx.status,
    contractAddress: tx.to_address ?? '',
    localContractId,
    data,
    decodedData,
  };
}

/**
 * Compares two ISO timestamps without losing sub-millisecond precision
 * (the backend stores microseconds; Date only keeps milliseconds).
 * Returns <0, 0 or >0 like a sort comparator. Unparseable values sort first.
 */
export function compareTimestamps(a: string, b: string): number {
  const parse = (value: string) => {
    const m = /^(.*?)(?:\.(\d+))?(Z|[+-]\d\d:?\d\d)?$/.exec(value ?? '');
    if (!m) return { ms: NaN, frac: '' };
    return {
      ms: Date.parse(`${m[1]}${m[3] ?? ''}`),
      frac: (m[2] ?? '').padEnd(9, '0'),
    };
  };
  const x = parse(a);
  const y = parse(b);
  if (Number.isNaN(x.ms) || Number.isNaN(y.ms)) {
    return Number.isNaN(x.ms) ? (Number.isNaN(y.ms) ? 0 : -1) : 1;
  }
  if (x.ms !== y.ms) return x.ms - y.ms;
  return x.frac < y.frac ? -1 : x.frac > y.frac ? 1 : 0;
}
