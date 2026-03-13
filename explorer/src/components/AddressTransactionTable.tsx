'use client';

import Link from 'next/link';
import { formatDistanceToNow } from 'date-fns';

import { Transaction } from '@/lib/types';
import { StatusBadge } from '@/components/StatusBadge';
import { TransactionTypeLabel } from '@/components/TransactionTypeLabel';
import { CopyButton } from '@/components/CopyButton';
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table';
import { CardContent } from '@/components/ui/card';
import { formatGenValue, truncateAddress } from '@/lib/formatters';
import { decodeCalldata } from '@/lib/resultDecoder';
import { getExecutionResult } from '@/lib/transactionUtils';

interface AddressTransactionTableProps {
  transactions: Transaction[];
  address: string;
}

export function AddressTransactionTable({ transactions, address }: AddressTransactionTableProps) {
  if (transactions.length === 0) {
    return (
      <CardContent className="py-8 text-center text-muted-foreground italic">
        No transactions found
      </CardContent>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow className="bg-muted/50">
          <TableHead>Hash</TableHead>
          <TableHead>Type</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Direction</TableHead>
          <TableHead>From</TableHead>
          <TableHead>To</TableHead>
          <TableHead className="w-32 max-w-32">Method</TableHead>
          <TableHead>GenVM Result</TableHead>
          <TableHead>Value</TableHead>
          <TableHead>Time</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {transactions.map((tx) => {
          const isIncoming = tx.to_address === address;
          const isOutgoing = tx.from_address === address;
          const calldataB64 = (tx.type === 1 || tx.type === 2) && tx.data && typeof tx.data === 'object'
            ? (tx.data as Record<string, unknown>).calldata as string | undefined
            : undefined;
          const decodedInput = calldataB64 ? decodeCalldata(calldataB64) : null;
          const methodName = decodedInput?.methodName ?? (decodedInput && !decodedInput.methodName ? '(constructor)' : undefined);
          const execResult = getExecutionResult(tx);
          const executionResult = execResult?.executionResult;

          return (
            <TableRow key={tx.hash}>
              <TableCell>
                <div className="flex items-center gap-1">
                  <Link
                    href={`/transactions/${tx.hash}`}
                    className="text-primary hover:underline font-mono text-sm"
                  >
                    {tx.hash.slice(0, 10)}...{tx.hash.slice(-8)}
                  </Link>
                  <CopyButton text={tx.hash} />
                </div>
              </TableCell>
              <TableCell>
                <TransactionTypeLabel type={tx.type} />
              </TableCell>
              <TableCell>
                <StatusBadge status={tx.status} />
              </TableCell>
              <TableCell>
                <div className="flex gap-1">
                  {isIncoming && (
                    <span className="bg-green-100 dark:bg-green-900 text-green-800 dark:text-green-300 text-xs px-2 py-0.5 rounded">
                      IN
                    </span>
                  )}
                  {isOutgoing && (
                    <span className="bg-red-100 dark:bg-red-900 text-red-800 dark:text-red-300 text-xs px-2 py-0.5 rounded">
                      OUT
                    </span>
                  )}
                </div>
              </TableCell>
              <TableCell className="font-mono text-sm">
                {tx.from_address ? (
                  <div className="flex items-center gap-1">
                    <Link href={`/address/${tx.from_address}`} className="text-primary hover:underline">
                      {truncateAddress(tx.from_address)}
                    </Link>
                    <CopyButton text={tx.from_address} />
                  </div>
                ) : (
                  <span className="text-muted-foreground">-</span>
                )}
              </TableCell>
              <TableCell className="font-mono text-sm">
                {tx.to_address ? (
                  <div className="flex items-center gap-1">
                    <Link href={`/address/${tx.to_address}`} className="text-primary hover:underline">
                      {truncateAddress(tx.to_address)}
                    </Link>
                    <CopyButton text={tx.to_address} />
                  </div>
                ) : (
                  <span className="text-muted-foreground">-</span>
                )}
              </TableCell>
              <TableCell className="text-sm font-mono w-32 max-w-32 truncate" title={methodName || undefined}>
                {methodName
                  ? <span className="text-foreground">{methodName}</span>
                  : <span className="text-muted-foreground">-</span>}
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
              <TableCell className="text-sm">
                {formatGenValue(tx.value)}
              </TableCell>
              <TableCell className="text-sm text-muted-foreground">
                {tx.created_at
                  ? formatDistanceToNow(new Date(tx.created_at), { addSuffix: true })
                  : '-'}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
