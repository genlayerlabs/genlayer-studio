'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { StatusBadge } from '@/components/StatusBadge';
import { TransactionTable } from '@/components/TransactionTable';
import { StatCard } from '@/components/StatCard';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Transaction, TransactionStatus } from '@/lib/types';
import { TRANSACTION_STATUS_DISPLAY_ORDER } from '@/lib/constants';
import {
  ArrowRightLeft,
  Users,
  Database,
  AlertTriangle,
  TrendingUp,
  Clock,
  Loader2,
  ChevronRight,
} from 'lucide-react';

interface Stats {
  totalTransactions: number;
  transactionsByStatus: Record<TransactionStatus, number>;
  transactionsByType: Record<string, number>;
  totalValidators: number;
  totalContracts: number;
  appealedTransactions: number;
  recentTransactions: Transaction[];
}

export default function Dashboard() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function fetchStats() {
      try {
        const res = await fetch('/api/stats');
        if (!res.ok) throw new Error('Failed to fetch stats');
        const data = await res.json();
        setStats(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    }
    fetchStats();
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
      </div>
    );
  }

  if (error) {
    return (
      <Card className="border-destructive">
        <CardContent className="p-6">
          <h2 className="font-bold mb-2 text-destructive">Error loading dashboard</h2>
          <p className="text-destructive/80">{error}</p>
        </CardContent>
      </Card>
    );
  }

  if (!stats) return null;

  const aggregatedTypes: { label: string; count: number; color: string }[] = [
    { label: 'Contract Deploy', count: stats.transactionsByType['deploy'] || 0, color: 'bg-orange-500' },
    { label: 'Contract Call', count: stats.transactionsByType['call'] || 0, color: 'bg-violet-500' },
  ];

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-foreground">Dashboard</h1>
        <p className="text-muted-foreground mt-1">Overview of GenLayer state and transactions</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="Total Transactions"
          value={stats.totalTransactions.toLocaleString()}
          icon={ArrowRightLeft}
          color="text-blue-600 dark:text-blue-400"
          iconBg="bg-blue-50 dark:bg-blue-950"
          href="/transactions"
        />
        <StatCard
          title="Active Validators"
          value={stats.totalValidators.toLocaleString()}
          icon={Users}
          color="text-emerald-600 dark:text-emerald-400"
          iconBg="bg-emerald-50 dark:bg-emerald-950"
          href="/validators"
        />
        <StatCard
          title="Contracts"
          value={stats.totalContracts.toLocaleString()}
          icon={Database}
          color="text-violet-600 dark:text-violet-400"
          iconBg="bg-violet-50 dark:bg-violet-950"
          href="/state"
        />
        <StatCard
          title="Appealed Transactions"
          value={stats.appealedTransactions.toLocaleString()}
          icon={AlertTriangle}
          color="text-amber-600 dark:text-amber-400"
          iconBg="bg-amber-50 dark:bg-amber-950"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-3">
              <div className="bg-blue-50 dark:bg-blue-950 p-2 rounded-lg">
                <TrendingUp className="w-5 h-5 text-blue-600 dark:text-blue-400" />
              </div>
              <CardTitle className="text-lg">Transaction Status Distribution</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {TRANSACTION_STATUS_DISPLAY_ORDER.map((status) => {
                const count = stats.transactionsByStatus[status] || 0;
                const percentage = stats.totalTransactions > 0
                  ? ((count / stats.totalTransactions) * 100).toFixed(1)
                  : 0;

                if (count === 0) return null;

                return (
                  <div key={status} className="flex items-center gap-3">
                    <div className="w-32 flex-shrink-0">
                      <StatusBadge status={status} />
                    </div>
                    <div className="flex-1 bg-muted rounded-full h-2.5 overflow-hidden">
                      <div
                        className="bg-primary h-2.5 rounded-full transition-all duration-500"
                        style={{ width: `${percentage}%` }}
                      />
                    </div>
                    <span className="text-sm font-medium text-foreground w-16 text-right">{count}</span>
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-3">
              <div className="bg-violet-50 dark:bg-violet-950 p-2 rounded-lg">
                <Database className="w-5 h-5 text-violet-600 dark:text-violet-400" />
              </div>
              <CardTitle className="text-lg">Transaction Types</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {aggregatedTypes.map(({ label, count, color }) => {
                const percentage = stats.totalTransactions > 0
                  ? ((count / stats.totalTransactions) * 100).toFixed(1)
                  : 0;

                return (
                  <div key={label} className="space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium text-foreground">{label}</span>
                      <span className="text-sm text-muted-foreground">
                        {count.toLocaleString()} <span className="text-muted-foreground/60">({percentage}%)</span>
                      </span>
                    </div>
                    <div className="bg-muted rounded-full h-3 overflow-hidden">
                      <div
                        className={`${color} h-3 rounded-full transition-all duration-500`}
                        style={{ width: `${percentage}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>
      </div>

      <Card className="overflow-hidden">
        <CardHeader className="pb-0">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="bg-muted p-2 rounded-lg">
                <Clock className="w-5 h-5 text-muted-foreground" />
              </div>
              <CardTitle className="text-lg">Recent Transactions</CardTitle>
            </div>
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
            transactions={stats.recentTransactions}
            showRelations={false}
          />
        </CardContent>
      </Card>
    </div>
  );
}
