'use client';

import { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { formatDistanceToNow } from 'date-fns';
import { ArrowUpRight, ArrowDownRight, Settings2 } from 'lucide-react';

import { StatusBadge } from '@/components/StatusBadge';
import { CopyButton } from '@/components/CopyButton';
import { TransactionTypeLabel } from '@/components/TransactionTypeLabel';
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import { Transaction } from '@/lib/types';
import { getTimeToAccepted, getTimeToFinalized, getExecutionResult } from '@/lib/transactionUtils';
import { truncateHash, truncateAddress } from '@/lib/formatters';
import { cn } from '@/lib/utils';

const STORAGE_KEY = 'explorer:tx-table-columns';

interface ColumnDef {
  id: string;
  label: string;
  defaultVisible: boolean;
  alwaysVisible?: boolean;
}

const OPTIONAL_COLUMNS: ColumnDef[] = [
  { id: 'hash', label: 'Hash', defaultVisible: true, alwaysVisible: true },
  { id: 'relations', label: 'Relations', defaultVisible: true },
  { id: 'type', label: 'Type', defaultVisible: true },
  { id: 'status', label: 'Status', defaultVisible: true },
  { id: 'from', label: 'From', defaultVisible: true },
  { id: 'to', label: 'To', defaultVisible: true },
  { id: 'genvmResult', label: 'GenVM Result', defaultVisible: true },
  { id: 'time', label: 'Time', defaultVisible: true },
  { id: 'accepted', label: 'Accepted', defaultVisible: true },
  { id: 'finalized', label: 'Finalized', defaultVisible: true },
  { id: 'blockedAt', label: 'Blocked At', defaultVisible: false },
  { id: 'worker', label: 'Worker', defaultVisible: false },
];

function getDefaultVisibility(): Record<string, boolean> {
  const defaults: Record<string, boolean> = {};
  for (const col of OPTIONAL_COLUMNS) {
    defaults[col.id] = col.defaultVisible;
  }
  return defaults;
}

function loadColumnVisibility(): Record<string, boolean> {
  if (typeof window === 'undefined') return getDefaultVisibility();
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      // Merge with defaults so new columns get their default value
      const defaults = getDefaultVisibility();
      return { ...defaults, ...parsed };
    }
  } catch {
    // ignore
  }
  return getDefaultVisibility();
}

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
  const [columnVisibility, setColumnVisibility] = useState<Record<string, boolean>>(getDefaultVisibility);
  const [showColumnPicker, setShowColumnPicker] = useState(false);

  // Load from localStorage on mount
  useEffect(() => {
    setColumnVisibility(loadColumnVisibility());
  }, []);

  const toggleColumn = useCallback((columnId: string) => {
    setColumnVisibility(prev => {
      const next = { ...prev, [columnId]: !prev[columnId] };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  const isVisible = (columnId: string) => {
    if (columnId === 'relations' && !showRelations) return false;
    return columnVisibility[columnId] !== false;
  };

  const visibleCount = OPTIONAL_COLUMNS.filter(c =>
    c.id === 'relations' ? showRelations && isVisible(c.id) : isVisible(c.id)
  ).length;

  return (
    <div>
      {/* Column picker */}
      <div className="flex justify-end px-4 py-2 border-b border-border">
        <div className="relative">
          <Button
            variant="ghost"
            size="sm"
            className="text-xs text-muted-foreground gap-1.5"
            onClick={() => setShowColumnPicker(!showColumnPicker)}
          >
            <Settings2 className="w-3.5 h-3.5" />
            Columns
          </Button>
          {showColumnPicker && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setShowColumnPicker(false)} />
              <div className="absolute right-0 top-full mt-1 z-50 bg-popover border border-border rounded-lg shadow-lg p-2 min-w-[160px]">
                {OPTIONAL_COLUMNS.filter(c => c.id === 'relations' ? showRelations : true).map(col => (
                  <label
                    key={col.id}
                    className={cn(
                      'flex items-center gap-2 px-2 py-1.5 rounded text-sm cursor-pointer hover:bg-accent',
                      col.alwaysVisible && 'opacity-50 cursor-default'
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={isVisible(col.id)}
                      disabled={col.alwaysVisible}
                      onChange={() => !col.alwaysVisible && toggleColumn(col.id)}
                      className="rounded border-border"
                    />
                    {col.label}
                  </label>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      <Table>
        <TableHeader>
          <TableRow className="bg-muted/50">
            {isVisible('hash') && <TableHead>Hash</TableHead>}
            {isVisible('relations') && <TableHead title="Parent and triggered transactions">Relations</TableHead>}
            {isVisible('type') && <TableHead>Type</TableHead>}
            {isVisible('status') && <TableHead>Status</TableHead>}
            {isVisible('from') && <TableHead>From</TableHead>}
            {isVisible('to') && <TableHead>To</TableHead>}
            {isVisible('genvmResult') && <TableHead>GenVM Result</TableHead>}
            {isVisible('time') && <TableHead>Time</TableHead>}
            {isVisible('accepted') && <TableHead title="Time from PENDING to ACCEPTED">Accepted</TableHead>}
            {isVisible('finalized') && <TableHead title="Time from PENDING to FINALIZED">Finalized</TableHead>}
            {isVisible('blockedAt') && <TableHead title="When the transaction was blocked">Blocked At</TableHead>}
            {isVisible('worker') && <TableHead title="Worker that processed the transaction">Worker</TableHead>}
          </TableRow>
        </TableHeader>
        <TableBody>
          {transactions.length === 0 ? (
            <TableRow>
              <TableCell colSpan={visibleCount} className="text-center text-muted-foreground py-8">
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
                  {isVisible('hash') && (
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
                  )}
                  {isVisible('relations') && (
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
                  {isVisible('type') && (
                    <TableCell>
                      <TransactionTypeLabel type={tx.type} />
                    </TableCell>
                  )}
                  {isVisible('status') && (
                    <TableCell>
                      <StatusBadge status={tx.status} />
                    </TableCell>
                  )}
                  {isVisible('from') && (
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
                          <Link href={`/contracts/${tx.from_address}`} className="hover:text-primary">
                            {truncateAddress(tx.from_address)}
                          </Link>
                          <CopyButton text={tx.from_address} />
                        </div>
                      ) : (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </TableCell>
                  )}
                  {isVisible('to') && (
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
                          <Link href={`/contracts/${tx.to_address}`} className="hover:text-primary">
                            {truncateAddress(tx.to_address)}
                          </Link>
                          <CopyButton text={tx.to_address} />
                        </div>
                      ) : (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </TableCell>
                  )}
                  {isVisible('genvmResult') && (
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
                  )}
                  {isVisible('time') && (
                    <TableCell className="text-sm text-muted-foreground">
                      {tx.created_at
                        ? formatDistanceToNow(new Date(tx.created_at), { addSuffix: true })
                        : <span className="text-muted-foreground">-</span>}
                    </TableCell>
                  )}
                  {isVisible('accepted') && (
                    <TableCell className="text-sm text-muted-foreground font-mono">
                      {timeToAccepted ? (
                        <span className="text-emerald-600 dark:text-emerald-400">{timeToAccepted}</span>
                      ) : (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </TableCell>
                  )}
                  {isVisible('finalized') && (
                    <TableCell className="text-sm text-muted-foreground font-mono">
                      {timeToFinalized ? (
                        <span className="text-green-600 dark:text-green-400">{timeToFinalized}</span>
                      ) : (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </TableCell>
                  )}
                  {isVisible('blockedAt') && (
                    <TableCell className="text-sm text-muted-foreground">
                      {tx.blocked_at
                        ? formatDistanceToNow(new Date(tx.blocked_at), { addSuffix: true })
                        : <span className="text-muted-foreground">-</span>}
                    </TableCell>
                  )}
                  {isVisible('worker') && (
                    <TableCell className="text-sm text-muted-foreground font-mono">
                      {tx.worker_id
                        ? <span>{tx.worker_id.replace(/^worker-/, '')}</span>
                        : <span className="text-muted-foreground">-</span>}
                    </TableCell>
                  )}
                </TableRow>
              );
            })
          )}
        </TableBody>
      </Table>
    </div>
  );
}

// Re-export for backward compatibility
export { TransactionTypeLabel } from '@/components/TransactionTypeLabel';
