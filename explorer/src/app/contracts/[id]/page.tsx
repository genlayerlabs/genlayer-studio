'use client';

import { useEffect, useState, use } from 'react';
import Link from 'next/link';
import { formatDistanceToNow, format } from 'date-fns';

import { CurrentState, Transaction } from '@/lib/types';
import { StatusBadge } from '@/components/StatusBadge';
import { JsonViewer } from '@/components/JsonViewer';
import { CodeBlock } from '@/components/CodeBlock';
import { CopyButton } from '@/components/CopyButton';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table';
import { truncateAddress, formatGenValue } from '@/lib/formatters';
import {
  ArrowLeft,
  Loader2,
  Database,
  Wallet,
  Clock,
  ArrowRightLeft,
  FileCode,
} from 'lucide-react';

interface StateDetail {
  state: CurrentState;
  transactions: Transaction[];
  contract_code: string | null;
}

export default function StateDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [data, setData] = useState<StateDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function fetchState() {
      try {
        const res = await fetch(`/api/state/${encodeURIComponent(id)}`);
        if (!res.ok) {
          if (res.status === 404) throw new Error('State not found');
          throw new Error('Failed to fetch state');
        }
        const data = await res.json();
        setData(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    }
    fetchState();
  }, [id]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/contracts" className="flex items-center gap-2">
            <ArrowLeft className="w-4 h-4" />
            Back to contracts
          </Link>
        </Button>
        <Card className="border-destructive">
          <CardContent className="p-6">
            <h2 className="font-bold mb-2 text-destructive">Error</h2>
            <p className="text-destructive/80">{error}</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (!data) return null;

  const { state, transactions, contract_code } = data;

  return (
    <div className="space-y-6">
      <div>
        <Button variant="ghost" size="sm" asChild className="mb-4">
          <Link href="/contracts" className="flex items-center gap-2">
            <ArrowLeft className="w-4 h-4" />
            Back to contracts
          </Link>
        </Button>
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold text-foreground">Contract State</h1>
            <div className="flex items-center gap-3 mt-2">
              <code className="font-mono text-sm text-muted-foreground">{state.id}</code>
              <CopyButton text={state.id} iconSize="md" />
            </div>
          </div>
          <div className="bg-purple-100 dark:bg-purple-950 p-3 rounded-lg">
            <Database className="w-8 h-8 text-purple-600 dark:text-purple-400" />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card>
          <CardContent className="p-6">
            <div className="flex items-center gap-3">
              <div className="bg-green-100 dark:bg-green-950 p-2 rounded-lg">
                <Wallet className="w-5 h-5 text-green-600 dark:text-green-400" />
              </div>
              <div>
                <p className="text-muted-foreground text-sm">Balance</p>
                <p className="text-xl font-bold text-foreground">{formatGenValue(state.balance)}</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-6">
            <div className="flex items-center gap-3">
              <div className="bg-blue-100 dark:bg-blue-950 p-2 rounded-lg">
                <ArrowRightLeft className="w-5 h-5 text-blue-600 dark:text-blue-400" />
              </div>
              <div>
                <p className="text-muted-foreground text-sm">Related Transactions</p>
                <p className="text-xl font-bold text-foreground">{transactions.length}</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-6">
            <div className="flex items-center gap-3">
              <div className="bg-muted p-2 rounded-lg">
                <Clock className="w-5 h-5 text-muted-foreground" />
              </div>
              <div>
                <p className="text-muted-foreground text-sm">Last Updated</p>
                <p className="text-sm font-medium text-foreground">
                  {state.updated_at
                    ? formatDistanceToNow(new Date(state.updated_at), { addSuffix: true })
                    : 'Unknown'}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {contract_code && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileCode className="w-5 h-5 text-indigo-500" />
              Contract Source Code
            </CardTitle>
          </CardHeader>
          <CardContent>
            <CodeBlock code={contract_code} />
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Database className="w-5 h-5 text-purple-500" />
            State Data
          </CardTitle>
        </CardHeader>
        <CardContent>
          {state.data && Object.keys(state.data).length > 0 ? (
            <div className="bg-muted p-4 rounded-lg overflow-auto max-h-[500px]">
              <JsonViewer data={state.data} />
            </div>
          ) : (
            <div className="text-muted-foreground italic">No state data available</div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ArrowRightLeft className="w-5 h-5 text-blue-500" />
            Related Transactions
          </CardTitle>
        </CardHeader>
        <CardContent>
          {transactions.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Hash</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Direction</TableHead>
                  <TableHead>From</TableHead>
                  <TableHead>To</TableHead>
                  <TableHead>Nonce</TableHead>
                  <TableHead>Value</TableHead>
                  <TableHead>Time</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {transactions.map((tx) => {
                  const isIncoming = tx.to_address === state.id;
                  const isOutgoing = tx.from_address === state.id;

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
                            <Link href={`/contracts/${tx.from_address}`} className="text-primary hover:underline">
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
                            <Link href={`/contracts/${tx.to_address}`} className="text-primary hover:underline">
                              {truncateAddress(tx.to_address)}
                            </Link>
                            <CopyButton text={tx.to_address} />
                          </div>
                        ) : (
                          <span className="text-muted-foreground">-</span>
                        )}
                      </TableCell>
                      <TableCell className="text-sm font-mono">
                        {tx.nonce !== null ? tx.nonce : '-'}
                      </TableCell>
                      <TableCell className="text-sm">
                        {formatGenValue(tx.value)}
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {tx.created_at
                          ? format(new Date(tx.created_at), 'PPpp')
                          : '-'}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          ) : (
            <div className="text-muted-foreground italic text-center py-8">
              No related transactions found
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
