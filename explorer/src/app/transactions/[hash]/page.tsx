'use client';

import { useEffect, useState, useCallback, use } from 'react';
import Link from 'next/link';
import { Transaction } from '@/lib/types';
import { useTransactionPolling } from '@/hooks/useTransactionPolling';
import { StatusBadge } from '@/components/StatusBadge';
import { TransactionTypeLabel } from '@/components/TransactionTypeLabel';
import { CopyButton } from '@/components/CopyButton';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import {
  OverviewTab,
  MonitoringTab,
  ConsensusTab,
  DataTab,
  RelatedTab,
} from './components';
import {
  ArrowLeft,
  Clock,
  AlertTriangle,
  Link as LinkIcon,
  Loader2,
  FileCode,
  Hash,
  Cpu,
  Activity,
} from 'lucide-react';
import { isTerminalStatus } from '@/lib/constants';

interface TransactionDetail {
  transaction: Transaction;
  triggeredTransactions: Transaction[];
  parentTransaction: Transaction | null;
}

const TABS = [
  { id: 'overview', label: 'Overview', icon: Hash },
  { id: 'monitoring', label: 'Monitoring', icon: Activity },
  { id: 'consensus', label: 'Consensus', icon: Cpu },
  { id: 'data', label: 'Data', icon: FileCode },
  { id: 'related', label: 'Related', icon: LinkIcon },
];

export default function TransactionDetailPage({ params }: { params: Promise<{ hash: string }> }) {
  const { hash } = use(params);
  const [data, setData] = useState<TransactionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function fetchTransaction() {
      try {
        const res = await fetch(`/api/transactions/${hash}`);
        if (!res.ok) {
          if (res.status === 404) throw new Error('Transaction not found');
          throw new Error('Failed to fetch transaction');
        }
        const data = await res.json();
        setData(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    }
    fetchTransaction();
  }, [hash]);

  // Poll for updates when transaction is not in a terminal state
  // Pauses polling when the browser tab is hidden
  const stableSetData = useCallback((d: TransactionDetail) => setData(d), []);
  useTransactionPolling(hash, data, stableSetData);

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
          <Link href="/transactions" className="flex items-center gap-2">
            <ArrowLeft className="w-4 h-4" />
            Back to transactions
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

  const { transaction: tx, triggeredTransactions, parentTransaction } = data;
  const relatedCount = triggeredTransactions.length + (parentTransaction ? 1 : 0);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <Button variant="ghost" size="sm" asChild className="mb-4">
            <Link href="/transactions" className="flex items-center gap-2">
              <ArrowLeft className="w-4 h-4" />
              Back to transactions
            </Link>
          </Button>
          <h1 className="text-2xl font-bold text-foreground">Transaction Details</h1>
          <div className="flex items-center gap-3 mt-2">
            <code className="font-mono text-sm text-muted-foreground">{tx.hash}</code>
            <CopyButton text={tx.hash} iconSize="md" />
          </div>
        </div>
        <div className="flex flex-col items-end gap-2">
          <div className="flex items-center gap-2">
            <StatusBadge status={tx.status} />
            {!isTerminalStatus(tx.status) && (
              <span className="flex items-center gap-1.5 text-xs font-medium text-green-600 dark:text-green-400">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500" />
                </span>
                Live
              </span>
            )}
          </div>
          <TransactionTypeLabel type={tx.type} />
        </div>
      </div>

      {/* Alert Badges */}
      <AlertBadges transaction={tx} />

      {/* Tabs */}
      <Tabs defaultValue="overview">
        <TabsList>
          {TABS.map((tab) => {
            const Icon = tab.icon;
            const label = tab.id === 'related' ? `Related (${relatedCount})` : tab.label;

            return (
              <TabsTrigger key={tab.id} value={tab.id} className="flex items-center gap-1.5">
                <Icon className="w-4 h-4" />
                {label}
              </TabsTrigger>
            );
          })}
        </TabsList>

        <Card className="mt-4">
          <CardContent className="p-6">
            <TabsContent value="overview"><OverviewTab transaction={tx} /></TabsContent>
            <TabsContent value="monitoring"><MonitoringTab transaction={tx} /></TabsContent>
            <TabsContent value="consensus"><ConsensusTab transaction={tx} /></TabsContent>
            <TabsContent value="data"><DataTab transaction={tx} /></TabsContent>
            <TabsContent value="related">
              <RelatedTab
                parentTransaction={parentTransaction}
                triggeredTransactions={triggeredTransactions}
              />
            </TabsContent>
          </CardContent>
        </Card>
      </Tabs>
    </div>
  );
}

function AlertBadges({ transaction: tx }: { transaction: Transaction }) {
  if (!tx.appealed && !tx.appeal_undetermined && !tx.appeal_leader_timeout && !tx.appeal_validators_timeout) {
    return null;
  }

  return (
    <div className="flex flex-wrap gap-2">
      {tx.appealed && (
        <div className="flex items-center gap-2 bg-orange-50 dark:bg-orange-950 text-orange-700 dark:text-orange-400 px-3 py-2 rounded-lg border border-orange-200 dark:border-orange-800">
          <AlertTriangle className="w-4 h-4" />
          Transaction was appealed
        </div>
      )}
      {tx.appeal_undetermined && (
        <div className="flex items-center gap-2 bg-yellow-50 dark:bg-yellow-950 text-yellow-700 dark:text-yellow-400 px-3 py-2 rounded-lg border border-yellow-200 dark:border-yellow-800">
          <AlertTriangle className="w-4 h-4" />
          Appeal undetermined
        </div>
      )}
      {tx.appeal_leader_timeout && (
        <div className="flex items-center gap-2 bg-red-50 dark:bg-red-950 text-red-700 dark:text-red-400 px-3 py-2 rounded-lg border border-red-200 dark:border-red-800">
          <Clock className="w-4 h-4" />
          Leader timeout on appeal
        </div>
      )}
      {tx.appeal_validators_timeout && (
        <div className="flex items-center gap-2 bg-red-50 dark:bg-red-950 text-red-700 dark:text-red-400 px-3 py-2 rounded-lg border border-red-200 dark:border-red-800">
          <Clock className="w-4 h-4" />
          Validators timeout on appeal
        </div>
      )}
    </div>
  );
}
