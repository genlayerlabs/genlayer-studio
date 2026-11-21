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
