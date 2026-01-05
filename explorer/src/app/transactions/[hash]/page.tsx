'use client';

import { useEffect, useState, use } from 'react';
import Link from 'next/link';
import { Transaction } from '@/lib/types';
import { StatusBadge } from '@/components/StatusBadge';
import { TransactionTypeLabel } from '@/components/TransactionTypeLabel';
import { CopyButton } from '@/components/CopyButton';
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

interface TransactionDetail {
  transaction: Transaction;
  triggeredTransactions: Transaction[];
  parentTransaction: Transaction | null;
}

type TabId = 'overview' | 'monitoring' | 'consensus' | 'data' | 'related';

const TABS = [
  { id: 'overview' as TabId, label: 'Overview', icon: Hash },
  { id: 'monitoring' as TabId, label: 'Monitoring', icon: Activity },
  { id: 'consensus' as TabId, label: 'Consensus', icon: Cpu },
  { id: 'data' as TabId, label: 'Data', icon: FileCode },
  { id: 'related' as TabId, label: 'Related', icon: LinkIcon },
];

export default function TransactionDetailPage({ params }: { params: Promise<{ hash: string }> }) {
  const { hash } = use(params);
  const [data, setData] = useState<TransactionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabId>('overview');

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

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-4">
        <Link href="/transactions" className="flex items-center gap-2 text-gray-600 hover:text-gray-800">
          <ArrowLeft className="w-4 h-4" />
          Back to transactions
        </Link>
        <div className="bg-red-50 border border-red-200 rounded-lg p-6 text-red-700">
          <h2 className="font-bold mb-2">Error</h2>
          <p>{error}</p>
        </div>
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
          <Link href="/transactions" className="flex items-center gap-2 text-gray-600 hover:text-gray-800 mb-4">
            <ArrowLeft className="w-4 h-4" />
            Back to transactions
          </Link>
          <h1 className="text-2xl font-bold text-gray-800">Transaction Details</h1>
          <div className="flex items-center gap-3 mt-2">
            <code className="font-mono text-sm text-gray-600">{tx.hash}</code>
            <CopyButton text={tx.hash} iconSize="md" />
          </div>
        </div>
        <div className="flex flex-col items-end gap-2">
          <StatusBadge status={tx.status} />
          <TransactionTypeLabel type={tx.type} contractSnapshot={tx.contract_snapshot} />
        </div>
      </div>

      {/* Alert Badges */}
      <AlertBadges transaction={tx} />

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="flex gap-8">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            const label = tab.id === 'related' ? `Related (${relatedCount})` : tab.label;

            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-2 py-4 border-b-2 text-sm font-medium transition-colors ${
                  activeTab === tab.id
                    ? 'border-blue-500 text-blue-600'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                }`}
              >
                <Icon className="w-4 h-4" />
                {label}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Tab Content */}
      <div className="bg-white rounded-xl shadow-sm p-6">
        {activeTab === 'overview' && <OverviewTab transaction={tx} />}
        {activeTab === 'monitoring' && <MonitoringTab transaction={tx} />}
        {activeTab === 'consensus' && <ConsensusTab transaction={tx} />}
        {activeTab === 'data' && <DataTab transaction={tx} />}
        {activeTab === 'related' && (
          <RelatedTab
            parentTransaction={parentTransaction}
            triggeredTransactions={triggeredTransactions}
          />
        )}
      </div>
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
        <div className="flex items-center gap-2 bg-orange-50 text-orange-700 px-3 py-2 rounded-lg">
          <AlertTriangle className="w-4 h-4" />
          Transaction was appealed
        </div>
      )}
      {tx.appeal_undetermined && (
        <div className="flex items-center gap-2 bg-yellow-50 text-yellow-700 px-3 py-2 rounded-lg">
          <AlertTriangle className="w-4 h-4" />
          Appeal undetermined
        </div>
      )}
      {tx.appeal_leader_timeout && (
        <div className="flex items-center gap-2 bg-red-50 text-red-700 px-3 py-2 rounded-lg">
          <Clock className="w-4 h-4" />
          Leader timeout on appeal
        </div>
      )}
      {tx.appeal_validators_timeout && (
        <div className="flex items-center gap-2 bg-red-50 text-red-700 px-3 py-2 rounded-lg">
          <Clock className="w-4 h-4" />
          Validators timeout on appeal
        </div>
      )}
    </div>
  );
}
