'use client';

import { useState } from 'react';
import Link from 'next/link';
import { formatDistanceToNow } from 'date-fns';
import { ArrowUpRight, ArrowDownRight } from 'lucide-react';

import { StatusBadge } from '@/components/StatusBadge';
import { CopyButton } from '@/components/CopyButton';
import { TransactionTypeLabel } from '@/components/TransactionTypeLabel';
import { Transaction } from '@/lib/types';
import { getTimeToAccepted, getTimeToFinalized, getExecutionResult } from '@/lib/transactionUtils';
import { truncateHash, truncateAddress } from '@/lib/formatters';

interface TransactionTableProps {
  transactions: Transaction[];
  showRelations?: boolean;
  onHighlightParent?: (parentHash: string | null) => void;
  onHighlightChildren?: (parentHash: string) => void;
  onClearHighlights?: () => void;
  highlightedHashes?: Set<string>;
}

export function TransactionTable({
  transactions,
  showRelations = true,
  onHighlightParent,
  onHighlightChildren,
  onClearHighlights,
  highlightedHashes = new Set(),
}: TransactionTableProps) {
  const [highlightedAddress, setHighlightedAddress] = useState<string | null>(null);

  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead className="bg-slate-50 border-b border-slate-200">
          <tr>
            <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider">Hash</th>
            {showRelations && (
              <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider" title="Parent and triggered transactions">Relations</th>
            )}
            <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider">Type</th>
            <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider">Status</th>
            <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider">From</th>
            <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider">To</th>
            <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider">GenVM Result</th>
            <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider">Time</th>
            <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider" title="Time from PENDING to ACCEPTED">Accepted</th>
            <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider" title="Time from PENDING to FINALIZED">Finalized</th>
            <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider" title="When the transaction was blocked">Blocked At</th>
            <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase tracking-wider" title="Worker that processed the transaction">Worker</th>
          </tr>
        </thead>
        <tbody>
          {transactions.length === 0 ? (
            <tr>
              <td colSpan={showRelations ? 12 : 11} className="px-4 py-8 text-center text-slate-500">
                No transactions found
              </td>
            </tr>
          ) : (
            transactions.map((tx) => {
              const execResult = getExecutionResult(tx);
              const executionResult = execResult?.executionResult;
              const timeToAccepted = getTimeToAccepted(tx);
              const timeToFinalized = getTimeToFinalized(tx);

              return (
                <tr
                  key={tx.hash}
                  className={`border-t border-slate-100 transition-colors ${
                    highlightedHashes.has(tx.hash)
                      ? 'bg-yellow-100 hover:bg-yellow-150'
                      : 'hover:bg-slate-50'
                  }`}
                >
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1">
                      <Link
                        href={`/transactions/${tx.hash}`}
                        className="text-blue-600 hover:underline font-mono text-sm"
                      >
                        {truncateHash(tx.hash)}
                      </Link>
                      <CopyButton text={tx.hash} />
                    </div>
                  </td>
                  {showRelations && (
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        {tx.triggered_by_hash && (
                          <Link
                            href={`/transactions/${tx.triggered_by_hash}`}
                            className="flex items-center gap-1 bg-amber-50 text-amber-700 px-2 py-1 rounded-lg text-xs font-medium hover:bg-amber-100 transition-colors"
                            title={`Parent: ${tx.triggered_by_hash}`}
                            onMouseEnter={() => onHighlightParent?.(tx.triggered_by_hash)}
                            onMouseLeave={onClearHighlights}
                          >
                            <ArrowUpRight className="w-3 h-3" />
                            Parent
                          </Link>
                        )}
                        {tx.triggered_count !== undefined && tx.triggered_count > 0 && (
                          <Link
                            href={`/transactions?search=${tx.hash}`}
                            className="flex items-center gap-1 bg-blue-50 text-blue-700 px-2 py-1 rounded-lg text-xs font-medium hover:bg-blue-100 transition-colors"
                            title={`${tx.triggered_count} triggered transaction(s)`}
                            onMouseEnter={() => onHighlightChildren?.(tx.hash)}
                            onMouseLeave={onClearHighlights}
                          >
                            <ArrowDownRight className="w-3 h-3" />
                            {tx.triggered_count}
                          </Link>
                        )}
                        {!tx.triggered_by_hash && (!tx.triggered_count || tx.triggered_count === 0) && (
                          <span className="text-slate-400">-</span>
                        )}
                      </div>
                    </td>
                  )}
                  <td className="px-4 py-3">
                    <TransactionTypeLabel type={tx.type} contractSnapshot={tx.contract_snapshot} />
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={tx.status} />
                  </td>
                  <td className="px-4 py-3.5 font-mono text-sm text-slate-600">
                    {tx.from_address ? (
                      <div
                        className={`inline-flex items-center gap-1 px-1 -mx-1 rounded transition-colors ${
                          highlightedAddress === tx.from_address ? 'bg-cyan-100' : ''
                        }`}
                        onMouseEnter={() => setHighlightedAddress(tx.from_address)}
                        onMouseLeave={() => setHighlightedAddress(null)}
                      >
                        <Link
                          href={`/state/${tx.from_address}`}
                          className="hover:text-blue-600"
                        >
                          {truncateAddress(tx.from_address)}
                        </Link>
                        <CopyButton text={tx.from_address} />
                      </div>
                    ) : (
                      <span className="text-slate-400">-</span>
                    )}
                  </td>
                  <td className="px-4 py-3.5 font-mono text-sm text-slate-600">
                    {tx.to_address ? (
                      <div
                        className={`inline-flex items-center gap-1 px-1 -mx-1 rounded transition-colors ${
                          highlightedAddress === tx.to_address ? 'bg-cyan-100' : ''
                        }`}
                        onMouseEnter={() => setHighlightedAddress(tx.to_address)}
                        onMouseLeave={() => setHighlightedAddress(null)}
                      >
                        <Link
                          href={`/state/${tx.to_address}`}
                          className="hover:text-blue-600"
                        >
                          {truncateAddress(tx.to_address)}
                        </Link>
                        <CopyButton text={tx.to_address} />
                      </div>
                    ) : (
                      <span className="text-slate-400">-</span>
                    )}
                  </td>
                  <td className="px-4 py-3.5 text-sm">
                    {executionResult ? (
                      executionResult === 'SUCCESS' ? (
                        <span className="bg-green-50 text-green-700 px-2 py-1 rounded-lg text-xs font-semibold">SUCCESS</span>
                      ) : (
                        <span className="bg-red-50 text-red-700 px-2 py-1 rounded-lg text-xs font-semibold">{executionResult}</span>
                      )
                    ) : (
                      <span className="text-slate-400">-</span>
                    )}
                  </td>
                  <td className="px-4 py-3.5 text-sm text-slate-600">
                    {tx.created_at
                      ? formatDistanceToNow(new Date(tx.created_at), { addSuffix: true })
                      : <span className="text-slate-400">-</span>}
                  </td>
                  <td className="px-4 py-3.5 text-sm text-slate-600 font-mono">
                    {timeToAccepted ? (
                      <span className="text-emerald-600">{timeToAccepted}</span>
                    ) : (
                      <span className="text-slate-400">-</span>
                    )}
                  </td>
                  <td className="px-4 py-3.5 text-sm text-slate-600 font-mono">
                    {timeToFinalized ? (
                      <span className="text-green-600">{timeToFinalized}</span>
                    ) : (
                      <span className="text-slate-400">-</span>
                    )}
                  </td>
                  <td className="px-4 py-3.5 text-sm text-slate-600">
                    {tx.blocked_at
                      ? formatDistanceToNow(new Date(tx.blocked_at), { addSuffix: true })
                      : <span className="text-slate-400">-</span>}
                  </td>
                  <td className="px-4 py-3.5 text-sm text-slate-600 font-mono">
                    {tx.worker_id
                      ? <span>{tx.worker_id.replace(/^worker-/, '')}</span>
                      : <span className="text-slate-400">-</span>}
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}

// Re-export for backward compatibility
export { TransactionTypeLabel } from '@/components/TransactionTypeLabel';
