import { cache } from 'react';
import Link from 'next/link';
import { fetchBackend } from '@/lib/fetchBackend';
import { StatCard } from '@/components/StatCard';
import { SparklineChart } from '@/components/SparklineChart';
import { StatusBadge } from '@/components/StatusBadge';
import { TransactionTable } from '@/components/TransactionTable';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { TRANSACTION_STATUS_DISPLAY_ORDER } from '@/lib/constants';
import { Transaction, TransactionStatus } from '@/lib/types';
import { ChevronRight } from 'lucide-react';

// ---------------------------------------------------------------------------
// Cached fetcher — React cache() deduplicates within a single render pass,
// so StatCardsSection and ChartsSection share one /stats call.
// ---------------------------------------------------------------------------

interface StatsData {
  totalTransactions: number;
  transactionsByStatus: Record<TransactionStatus, number>;
  transactionsByType: Record<string, number>;
  totalValidators: number;
  totalContracts: number;
  appealedTransactions: number;
  finalizedTransactions: number;
  avgTps24h: number;
  txVolume14d: { date: string; count: number }[];
  recentTransactions: Transaction[];
}

const getStats = cache(() => fetchBackend<StatsData>('/stats'));

// ---------------------------------------------------------------------------
// 1. Stat Cards
// ---------------------------------------------------------------------------

export async function StatCardsSection() {
  const stats = await getStats();

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
      <StatCard
        title="Total Transactions"
        value={stats.totalTransactions.toLocaleString()}
        icon="ArrowRightLeft"
        color="text-blue-600 dark:text-blue-400"
        iconBg="bg-blue-50 dark:bg-blue-950"
        href="/transactions"
      />
      <StatCard
        title="Active Validators"
        value={stats.totalValidators.toLocaleString()}
        icon="Users"
        color="text-emerald-600 dark:text-emerald-400"
        iconBg="bg-emerald-50 dark:bg-emerald-950"
        href="/validators"
      />
      <StatCard
        title="Contracts"
        value={stats.totalContracts.toLocaleString()}
        icon="Database"
        color="text-violet-600 dark:text-violet-400"
        iconBg="bg-violet-50 dark:bg-violet-950"
        href="/contracts"
      />
      <StatCard
        title="Avg TPS (24h)"
        value={stats.avgTps24h.toFixed(4)}
        icon="Zap"
        color="text-cyan-600 dark:text-cyan-400"
        iconBg="bg-cyan-50 dark:bg-cyan-950"
      />
      <StatCard
        title="Appealed Transactions"
        value={stats.appealedTransactions.toLocaleString()}
        icon="AlertTriangle"
        color="text-amber-600 dark:text-amber-400"
        iconBg="bg-amber-50 dark:bg-amber-950"
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 2. Charts — status distribution, types, volume
// ---------------------------------------------------------------------------

export async function ChartsSection() {
  const stats = await getStats();

  const aggregatedTypes = [
    { label: 'Contract Deploy', count: stats.transactionsByType['deploy'] || 0, color: 'bg-orange-500' },
    { label: 'Contract Call', count: stats.transactionsByType['call'] || 0, color: 'bg-violet-500' },
  ];

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <Card className="py-4">
        <CardHeader className="px-4 pb-2 pt-0">
          <CardTitle className="text-sm font-medium text-muted-foreground">Status Distribution</CardTitle>
        </CardHeader>
        <CardContent className="px-4 pb-0">
          <div className="space-y-2">
            {TRANSACTION_STATUS_DISPLAY_ORDER.map((status) => {
              const count = stats.transactionsByStatus[status] || 0;
              const percentage = stats.totalTransactions > 0
                ? ((count / stats.totalTransactions) * 100).toFixed(1)
                : 0;

              if (count === 0) return null;

              return (
                <div key={status} className="flex items-center gap-2">
                  <div className="w-28 flex-shrink-0">
                    <StatusBadge status={status} />
                  </div>
                  <div className="flex-1 bg-muted rounded-full h-2 overflow-hidden">
                    <div
                      className="bg-primary h-2 rounded-full transition-all duration-500"
                      style={{ width: `${percentage}%` }}
                    />
                  </div>
                  <span className="text-xs font-medium text-foreground w-12 text-right">{count}</span>
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      <Card className="py-4">
        <CardHeader className="px-4 pb-2 pt-0">
          <CardTitle className="text-sm font-medium text-muted-foreground">Transaction Types</CardTitle>
        </CardHeader>
        <CardContent className="px-4 pb-0">
          <div className="space-y-3">
            {aggregatedTypes.map(({ label, count, color }) => {
              const percentage = stats.totalTransactions > 0
                ? ((count / stats.totalTransactions) * 100).toFixed(1)
                : 0;

              return (
                <div key={label} className="space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-foreground">{label}</span>
                    <span className="text-xs text-muted-foreground">
                      {count.toLocaleString()} ({percentage}%)
                    </span>
                  </div>
                  <div className="bg-muted rounded-full h-2 overflow-hidden">
                    <div
                      className={`${color} h-2 rounded-full transition-all duration-500`}
                      style={{ width: `${percentage}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {stats.txVolume14d.length > 0 && (
        <Card className="py-4">
          <CardHeader className="px-4 pb-2 pt-0">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-medium text-muted-foreground">Volume (14d)</CardTitle>
              <span className="text-xs text-muted-foreground">
                {stats.txVolume14d.reduce((s, d) => s + d.count, 0).toLocaleString()} txs
              </span>
            </div>
          </CardHeader>
          <CardContent className="px-4 pb-0">
            <div className="text-cyan-600 dark:text-cyan-400">
              <SparklineChart
                data={stats.txVolume14d.map(d => d.count)}
                width={400}
                height={60}
                className="w-full"
              />
            </div>
            <div className="flex justify-between mt-1 text-xs text-muted-foreground">
              <span>{stats.txVolume14d[0]?.date}</span>
              <span>{stats.txVolume14d[stats.txVolume14d.length - 1]?.date}</span>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 3. Recent Transactions — independent fetch from /transactions
// ---------------------------------------------------------------------------

interface TransactionsResponse {
  transactions: Transaction[];
}

export async function RecentTransactionsSection() {
  const data = await fetchBackend<TransactionsResponse>('/transactions?limit=10');

  return (
    <Card className="overflow-hidden">
      <CardHeader className="pb-0">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm font-medium text-muted-foreground">Recent Transactions</CardTitle>
          <Button variant="ghost" size="sm" asChild>
            <Link href="/transactions" className="flex items-center gap-1">
              View all
              <ChevronRight className="w-4 h-4" />
            </Link>
          </Button>
        </div>
      </CardHeader>
      <CardContent className="p-0 pt-4">
        <TransactionTable
          transactions={data.transactions}
          showRelations={false}
        />
      </CardContent>
    </Card>
  );
}
