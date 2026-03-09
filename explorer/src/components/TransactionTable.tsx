'use client';

import { useState } from 'react';
import Link from 'next/link';
import { formatDistanceToNow } from 'date-fns';
import { ArrowUpRight, ArrowDownRight } from 'lucide-react';

import { StatusBadge } from '@/components/StatusBadge';
import { CopyButton } from '@/components/CopyButton';
import { TransactionTypeLabel } from '@/components/TransactionTypeLabel';
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table';
import { Transaction } from '@/lib/types';
import { getTimeToAccepted, getTimeToFinalized, getExecutionResult } from '@/lib/transactionUtils';
import { truncateHash, truncateAddress } from '@/lib/formatters';
import { cn } from '@/lib/utils';

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
    <Table>
      <TableHeader>
        <TableRow className="bg-muted/50">
          <TableHead>Hash</TableHead>
          {showRelations && <TableHead title="Parent and triggered transactions">Relations</TableHead>}
          <TableHead>Type</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>From</TableHead>
          <TableHead>To</TableHead>
          <TableHead>GenVM Result</TableHead>
          <TableHead>Time</TableHead>
          <TableHead title="Time from PENDING to ACCEPTED">Accepted</TableHead>
          <TableHead title="Time from PENDING to FINALIZED">Finalized</TableHead>
          <TableHead title="When the transaction was blocked">Blocked At</TableHead>
          <TableHead title="Worker that processed the transaction">Worker</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {transactions.length === 0 ? (
          <TableRow>
            <TableCell colSpan={showRelations ? 12 : 11} className="text-center text-muted-foreground py-8">
              No transactions found
            </TableCell>
          </TableRow>
        ) : (
          transactions.map((tx) => {
            const execResult = getExecutionResult(tx);
            const executionResult = execResult?.executionResult;
            const timeToAccepted = getTimeToAccepted(tx);
            const timeToFinalized = getTimeToFinalized(tx);

            return (
              <TableRow
                key={tx.hash}
                className={cn(
                  highlightedHashes.has(tx.hash) && 'bg-yellow-100 dark:bg-yellow-900/30 hover:bg-yellow-100 dark:hover:bg-yellow-900/40'
                )}
              >
                <TableCell>
                  <div className="flex items-center gap-1">
                    <Link
                      href={`/transactions/${tx.hash}`}
                      className="text-primary hover:underline font-mono text-sm"
                    >
                      {truncateHash(tx.hash)}
                    </Link>
                    <CopyButton text={tx.hash} />
                  </div>
                </TableCell>
                {showRelations && (
                  <TableCell>
                    <div className="flex items-center gap-2">
                      {tx.triggered_by_hash && (
                        <Link
                          href={`/transactions/${tx.triggered_by_hash}`}
                          className="flex items-center gap-1 bg-amber-50 dark:bg-amber-950 text-amber-700 dark:text-amber-400 px-2 py-1 rounded-lg text-xs font-medium hover:bg-amber-100 dark:hover:bg-amber-900 transition-colors"
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
                          className="flex items-center gap-1 bg-blue-50 dark:bg-blue-950 text-blue-700 dark:text-blue-400 px-2 py-1 rounded-lg text-xs font-medium hover:bg-blue-100 dark:hover:bg-blue-900 transition-colors"
                          title={`${tx.triggered_count} triggered transaction(s)`}
                          onMouseEnter={() => onHighlightChildren?.(tx.hash)}
                          onMouseLeave={onClearHighlights}
                        >
                          <ArrowDownRight className="w-3 h-3" />
                          {tx.triggered_count}
                        </Link>
                      )}
                      {!tx.triggered_by_hash && (!tx.triggered_count || tx.triggered_count === 0) && (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </div>
                  </TableCell>
                )}
                <TableCell>
                  <TransactionTypeLabel type={tx.type} />
                </TableCell>
                <TableCell>
                  <StatusBadge status={tx.status} />
                </TableCell>
                <TableCell className="font-mono text-sm text-muted-foreground">
                  {tx.from_address ? (
                    <div
                      className={cn(
                        'inline-flex items-center gap-1 px-1 -mx-1 rounded transition-colors',
                        highlightedAddress === tx.from_address && 'bg-cyan-100 dark:bg-cyan-900/40'
                      )}
                      onMouseEnter={() => setHighlightedAddress(tx.from_address)}
                      onMouseLeave={() => setHighlightedAddress(null)}
                    >
                      <Link href={`/state/${tx.from_address}`} className="hover:text-primary">
                        {truncateAddress(tx.from_address)}
                      </Link>
                      <CopyButton text={tx.from_address} />
                    </div>
                  ) : (
                    <span className="text-muted-foreground">-</span>
                  )}
                </TableCell>
                <TableCell className="font-mono text-sm text-muted-foreground">
                  {tx.to_address ? (
                    <div
                      className={cn(
                        'inline-flex items-center gap-1 px-1 -mx-1 rounded transition-colors',
                        highlightedAddress === tx.to_address && 'bg-cyan-100 dark:bg-cyan-900/40'
                      )}
                      onMouseEnter={() => setHighlightedAddress(tx.to_address)}
                      onMouseLeave={() => setHighlightedAddress(null)}
                    >
                      <Link href={`/state/${tx.to_address}`} className="hover:text-primary">
                        {truncateAddress(tx.to_address)}
                      </Link>
                      <CopyButton text={tx.to_address} />
                    </div>
                  ) : (
                    <span className="text-muted-foreground">-</span>
                  )}
                </TableCell>
                <TableCell className="text-sm">
                  {executionResult ? (
                    executionResult === 'SUCCESS' ? (
                      <span className="bg-green-50 dark:bg-green-950 text-green-700 dark:text-green-400 px-2 py-1 rounded-lg text-xs font-semibold">SUCCESS</span>
                    ) : (
                      <span className="bg-red-50 dark:bg-red-950 text-red-700 dark:text-red-400 px-2 py-1 rounded-lg text-xs font-semibold">{executionResult}</span>
                    )
                  ) : (
                    <span className="text-muted-foreground">-</span>
                  )}
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {tx.created_at
                    ? formatDistanceToNow(new Date(tx.created_at), { addSuffix: true })
                    : <span className="text-muted-foreground">-</span>}
                </TableCell>
                <TableCell className="text-sm text-muted-foreground font-mono">
                  {timeToAccepted ? (
                    <span className="text-emerald-600 dark:text-emerald-400">{timeToAccepted}</span>
                  ) : (
                    <span className="text-muted-foreground">-</span>
                  )}
                </TableCell>
                <TableCell className="text-sm text-muted-foreground font-mono">
                  {timeToFinalized ? (
                    <span className="text-green-600 dark:text-green-400">{timeToFinalized}</span>
                  ) : (
                    <span className="text-muted-foreground">-</span>
                  )}
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {tx.blocked_at
                    ? formatDistanceToNow(new Date(tx.blocked_at), { addSuffix: true })
                    : <span className="text-muted-foreground">-</span>}
                </TableCell>
                <TableCell className="text-sm text-muted-foreground font-mono">
                  {tx.worker_id
                    ? <span>{tx.worker_id.replace(/^worker-/, '')}</span>
                    : <span className="text-muted-foreground">-</span>}
                </TableCell>
              </TableRow>
            );
          })
        )}
      </TableBody>
    </Table>
  );
}

// Re-export for backward compatibility
export { TransactionTypeLabel } from '@/components/TransactionTypeLabel';
