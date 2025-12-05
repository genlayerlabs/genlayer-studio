'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { StatusBadge } from '@/components/StatusBadge';
import { TransactionTable } from '@/components/TransactionTable';
import { StatCard } from '@/components/StatCard';
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
        <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-xl p-6 text-red-800">
        <h2 className="font-bold mb-2">Error loading dashboard</h2>
        <p className="text-red-700">{error}</p>
      </div>
    );
  }

  if (!stats) return null;

  // Transaction types from API (already aggregated as deploy/call)
  const aggregatedTypes: { label: string; count: number; color: string }[] = [
    { label: 'Contract Deploy', count: stats.transactionsByType['deploy'] || 0, color: 'bg-orange-500' },
    { label: 'Contract Call', count: stats.transactionsByType['call'] || 0, color: 'bg-violet-500' },
  ];

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-slate-900">Dashboard</h1>
        <p className="text-slate-600 mt-1">Overview of GenLayer state and transactions</p>
      </div>

      {/* Main Stats */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="Total Transactions"
          value={stats.totalTransactions.toLocaleString()}
          icon={ArrowRightLeft}
          color="text-blue-600"
          iconBg="bg-blue-50"
          href="/transactions"
        />
        <StatCard
          title="Active Validators"
          value={stats.totalValidators.toLocaleString()}
          icon={Users}
          color="text-emerald-600"
          iconBg="bg-emerald-50"
          href="/validators"
        />
        <StatCard
          title="Contracts"
          value={stats.totalContracts.toLocaleString()}
          icon={Database}
          color="text-violet-600"
          iconBg="bg-violet-50"
          href="/state"
        />
        <StatCard
          title="Appealed Transactions"
          value={stats.appealedTransactions.toLocaleString()}
          icon={AlertTriangle}
          color="text-amber-600"
          iconBg="bg-amber-50"
        />
      </div>

      {/* Status Distribution & Types */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Transaction Status Distribution */}
        <div className="bg-white rounded-xl border border-slate-200 p-6">
          <div className="flex items-center gap-3 mb-6">
            <div className="bg-blue-50 p-2 rounded-lg">
              <TrendingUp className="w-5 h-5 text-blue-600" />
            </div>
            <h2 className="text-lg font-semibold text-slate-900">Transaction Status Distribution</h2>
          </div>
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
                  <div className="flex-1 bg-slate-100 rounded-full h-2.5 overflow-hidden">
                    <div
                      className="bg-blue-500 h-2.5 rounded-full transition-all duration-500"
                      style={{ width: `${percentage}%` }}
                    />
                  </div>
                  <span className="text-sm font-medium text-slate-700 w-16 text-right">{count}</span>
                </div>
              );
            })}
          </div>
        </div>

        {/* Transaction Types */}
        <div className="bg-white rounded-xl border border-slate-200 p-6">
          <div className="flex items-center gap-3 mb-6">
            <div className="bg-violet-50 p-2 rounded-lg">
              <Database className="w-5 h-5 text-violet-600" />
            </div>
            <h2 className="text-lg font-semibold text-slate-900">Transaction Types</h2>
          </div>
          <div className="space-y-4">
            {aggregatedTypes.map(({ label, count, color }) => {
              const percentage = stats.totalTransactions > 0
                ? ((count / stats.totalTransactions) * 100).toFixed(1)
                : 0;

              return (
                <div key={label} className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-slate-800">{label}</span>
                    <span className="text-sm text-slate-600">
                      {count.toLocaleString()} <span className="text-slate-400">({percentage}%)</span>
                    </span>
                  </div>
                  <div className="bg-slate-100 rounded-full h-3 overflow-hidden">
                    <div
                      className={`${color} h-3 rounded-full transition-all duration-500`}
                      style={{ width: `${percentage}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Recent Transactions */}
      <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        <div className="flex items-center justify-between p-6 pb-0">
          <div className="flex items-center gap-3">
            <div className="bg-slate-100 p-2 rounded-lg">
              <Clock className="w-5 h-5 text-slate-600" />
            </div>
            <h2 className="text-lg font-semibold text-slate-900">Recent Transactions</h2>
          </div>
          <Link
            href="/transactions"
            className="text-blue-600 hover:text-blue-700 text-sm font-medium flex items-center gap-1 hover:gap-2 transition-all"
          >
            View all
            <ChevronRight className="w-4 h-4" />
          </Link>
        </div>
        <TransactionTable
          transactions={stats.recentTransactions}
          showRelations={false}
        />
      </div>
    </div>
  );
}
