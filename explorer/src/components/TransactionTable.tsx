'use client';

import { useState, useEffect, useRef } from 'react';
import Link from '@/components/AppLink';
import { formatDistanceToNow } from 'date-fns';
import { ArrowUpRight, ArrowDownRight, Settings2 } from 'lucide-react';

import { StatusBadge } from '@/components/StatusBadge';
import { AddressDisplay } from '@/components/AddressDisplay';
import { TransactionTypeLabel } from '@/components/TransactionTypeLabel';
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import { Transaction } from '@/lib/types';
import { getTimeToAccepted, getTimeToFinalized, getExecutionResult } from '@/lib/transactionUtils';
import { decodeCalldata } from '@/lib/resultDecoder';
import { cn } from '@/lib/utils';
import { useColumnVisibility } from '@/hooks/useColumnVisibility';
import type { ColumnDef } from '@/hooks/useColumnVisibility';

const STORAGE_KEY = 'explorer:tx-table-columns';

const OPTIONAL_COLUMNS: ColumnDef[] = [
  { id: 'hash', label: 'Hash', defaultVisible: true, alwaysVisible: true },
  { id: 'type', label: 'Type', defaultVisible: true },
  { id: 'status', label: 'Status', defaultVisible: true },
  { id: 'from', label: 'From', defaultVisible: true },
  { id: 'to', label: 'To', defaultVisible: true },
  { id: 'method', label: 'Method', defaultVisible: true },
  { id: 'genvmResult', label: 'GenVM Result', defaultVisible: true },
  { id: 'time', label: 'Time', defaultVisible: true },
  { id: 'relations', label: 'Relations', defaultVisible: true },
  { id: 'accepted', label: 'Accepted', defaultVisible: false },
  { id: 'finalized', label: 'Finalized', defaultVisible: false },
  { id: 'blockedAt', label: 'Blocked At', defaultVisible: false },
  { id: 'worker', label: 'Worker', defaultVisible: false },
];

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
  const {
    isVisible: isColumnVisible,
    showColumnPicker,
    setShowColumnPicker,
    toggleColumn,
  } = useColumnVisibility(STORAGE_KEY, OPTIONAL_COLUMNS);
  const columnPickerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!showColumnPicker) return;
    const handler = (e: MouseEvent) => {
      if (columnPickerRef.current && !columnPickerRef.current.contains(e.target as Node)) {
        setShowColumnPicker(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [showColumnPicker, setShowColumnPicker]);

  const isVisible = (columnId: string) => {
    if (columnId === 'relations' && !showRelations) return false;
    return isColumnVisible(columnId);
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
              <div ref={columnPickerRef} className="absolute right-0 top-full mt-1 z-50 bg-popover border border-border rounded-lg shadow-lg p-2 min-w-[160px] max-h-72 overflow-y-auto">
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
          )}
        </div>
      </div>

      <Table>
        <TableHeader>
          <TableRow className="bg-muted/50">
            {isVisible('hash') && <TableHead>Hash</TableHead>}
            {isVisible('type') && <TableHead>Type</TableHead>}
            {isVisible('status') && <TableHead>Status</TableHead>}
            {isVisible('from') && <TableHead>From</TableHead>}
            {isVisible('to') && <TableHead>To</TableHead>}
            {isVisible('method') && <TableHead className="w-32 max-w-32">Method</TableHead>}
            {isVisible('genvmResult') && <TableHead>GenVM Result</TableHead>}
            {isVisible('time') && <TableHead>Time</TableHead>}
            {isVisible('relations') && <TableHead title="Parent and triggered transactions">Relations</TableHead>}
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
              const calldataB64 = (tx.type === 1 || tx.type === 2) && tx.data && typeof tx.data === 'object'
                ? (tx.data as Record<string, unknown>).calldata as string | undefined
                : undefined;
              const decodedInput = calldataB64 ? decodeCalldata(calldataB64) : null;
              const methodName = decodedInput?.methodName ?? (decodedInput && !decodedInput.methodName ? '(constructor)' : undefined);

              return (
                <TableRow
                  key={tx.hash}
                  className={cn(
                    highlightedHashes.has(tx.hash) && 'bg-yellow-100 dark:bg-yellow-900/30 hover:bg-yellow-100 dark:hover:bg-yellow-900/40'
                  )}
                >
                  {isVisible('hash') && (
                    <TableCell>
                      <AddressDisplay
                        address={tx.hash}
                        href={`/transactions/${tx.hash}`}
                        isHash
                        linkClassName="text-primary hover:underline font-mono text-sm"
                      />
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
                        <AddressDisplay
                          address={tx.from_address}
                          href={`/address/${tx.from_address}`}
                          highlight={highlightedAddress === tx.from_address}
                          onMouseEnter={() => setHighlightedAddress(tx.from_address)}
                          onMouseLeave={() => setHighlightedAddress(null)}
                        />
                      ) : (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </TableCell>
                  )}
                  {isVisible('to') && (
                    <TableCell className="font-mono text-sm text-muted-foreground">
                      {tx.to_address ? (
                        <AddressDisplay
                          address={tx.to_address}
                          href={`/address/${tx.to_address}`}
                          highlight={highlightedAddress === tx.to_address}
                          onMouseEnter={() => setHighlightedAddress(tx.to_address)}
                          onMouseLeave={() => setHighlightedAddress(null)}
                        />
                      ) : (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </TableCell>
                  )}
                  {isVisible('method') && (
                    <TableCell className="text-sm font-mono w-32 max-w-32 truncate" title={methodName || undefined}>
                      {methodName
                        ? <span className="text-foreground">{methodName}</span>
                        : <span className="text-muted-foreground">-</span>}
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
