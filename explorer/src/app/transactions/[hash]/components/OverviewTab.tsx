'use client';

import Link from 'next/link';
import { format } from 'date-fns';
import { Transaction } from '@/lib/types';
import { StatusBadge } from '@/components/StatusBadge';
import { TransactionTypeLabel } from '@/components/TransactionTypeLabel';
import { InfoRow } from '@/components/InfoRow';
import { getExecutionResult } from '@/lib/transactionUtils';

interface OverviewTabProps {
  transaction: Transaction;
}

export function OverviewTab({ transaction: tx }: OverviewTabProps) {
  const execResult = getExecutionResult(tx);
  const executionResult = execResult?.executionResult;
  const genvmResult = execResult?.genvmResult;

  return (
    <div className="space-y-1">
      <InfoRow label="Hash" value={tx.hash} copyable />
      <InfoRow label="Status" value={<StatusBadge status={tx.status} />} />
      <InfoRow label="Type" value={<TransactionTypeLabel type={tx.type} />} />
      <InfoRow
        label="From"
        value={
          tx.from_address ? (
            <Link href={`/state/${tx.from_address}`} className="text-blue-600 hover:underline">
              {tx.from_address}
            </Link>
          ) : (
            '-'
          )
        }
        copyable={!!tx.from_address}
        copyText={tx.from_address || undefined}
      />
      <InfoRow
        label="To"
        value={
          tx.to_address ? (
            <Link href={`/state/${tx.to_address}`} className="text-blue-600 hover:underline">
              {tx.to_address}
            </Link>
          ) : (
            '-'
          )
        }
        copyable={!!tx.to_address}
        copyText={tx.to_address || undefined}
      />
      <InfoRow label="Value" value={tx.value !== null ? tx.value.toString() : '-'} />
      <InfoRow label="Nonce" value={tx.nonce !== null ? tx.nonce.toString() : '-'} />
      <InfoRow label="Gas Limit" value={tx.gaslimit !== null ? tx.gaslimit.toString() : '-'} />
      <InfoRow
        label="Created At"
        value={tx.created_at ? format(new Date(tx.created_at), 'PPpp') : '-'}
      />
      <InfoRow
        label="Execution Mode"
        value={
          tx.execution_mode === 'LEADER_ONLY' ? (
            <span className="bg-yellow-50 text-yellow-700 px-2.5 py-1 rounded-lg text-xs font-semibold">Leader Only</span>
          ) : tx.execution_mode === 'LEADER_SELF_VALIDATOR' ? (
            <span className="bg-blue-50 text-blue-700 px-2.5 py-1 rounded-lg text-xs font-semibold">Leader + Self Validator</span>
          ) : (
            <span className="bg-green-50 text-green-700 px-2.5 py-1 rounded-lg text-xs font-semibold">Normal</span>
          )
        }
      />
      <InfoRow label="Rotation Count" value={tx.rotation_count?.toString() || '-'} />
      <InfoRow label="Initial Validators" value={tx.num_of_initial_validators?.toString() || '-'} />
      {tx.worker_id && <InfoRow label="Worker ID" value={tx.worker_id} />}

      {/* GenVM Execution Results */}
      {(executionResult || genvmResult) && (
        <>
          <div className="border-t border-gray-200 mt-4 pt-4">
            <h4 className="text-sm font-semibold text-gray-700 mb-3">GenVM Execution</h4>
          </div>
          {executionResult && (
            <InfoRow
              label="Execution Result"
              value={
                executionResult === 'SUCCESS' ? (
                  <span className="bg-green-50 text-green-700 px-2.5 py-1 rounded-lg text-xs font-semibold">SUCCESS</span>
                ) : (
                  <span className="bg-red-50 text-red-700 px-2.5 py-1 rounded-lg text-xs font-semibold">{executionResult}</span>
                )
              }
            />
          )}
          {genvmResult?.stdout !== undefined && (
            <InfoRow
              label="Stdout"
              value={genvmResult.stdout || <span className="text-slate-400">(empty)</span>}
              copyable={!!genvmResult.stdout}
              copyText={genvmResult.stdout}
            />
          )}
          {genvmResult?.stderr !== undefined && (
            <InfoRow
              label="Stderr"
              value={
                genvmResult.stderr ? (
                  <span className="text-red-600">{genvmResult.stderr}</span>
                ) : (
                  <span className="text-slate-400">(empty)</span>
                )
              }
              copyable={!!genvmResult.stderr}
              copyText={genvmResult.stderr}
            />
          )}
        </>
      )}
    </div>
  );
}
