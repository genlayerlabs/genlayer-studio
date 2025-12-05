'use client';

import { useEffect, useState, use } from 'react';
import Link from 'next/link';
import { formatDistanceToNow, format } from 'date-fns';

import { CurrentState, Transaction } from '@/lib/types';
import { StatusBadge } from '@/components/StatusBadge';
import { JsonViewer } from '@/components/JsonViewer';
import { CopyButton } from '@/components/CopyButton';
import { truncateAddress } from '@/lib/formatters';
import {
  ArrowLeft,
  Loader2,
  Database,
  Wallet,
  Clock,
  ArrowRightLeft,
} from 'lucide-react';

interface StateDetail {
  state: CurrentState;
  transactions: Transaction[];
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
        <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-4">
        <Link href="/state" className="flex items-center gap-2 text-gray-600 hover:text-gray-800">
          <ArrowLeft className="w-4 h-4" />
          Back to states
        </Link>
        <div className="bg-red-50 border border-red-200 rounded-lg p-6 text-red-700">
          <h2 className="font-bold mb-2">Error</h2>
          <p>{error}</p>
        </div>
      </div>
    );
  }

  if (!data) return null;

  const { state, transactions } = data;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <Link href="/state" className="flex items-center gap-2 text-gray-600 hover:text-gray-800 mb-4">
          <ArrowLeft className="w-4 h-4" />
          Back to states
        </Link>
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-800">Contract State</h1>
            <div className="flex items-center gap-3 mt-2">
              <code className="font-mono text-sm text-gray-600">{state.id}</code>
              <CopyButton text={state.id} iconSize="md" />
            </div>
          </div>
          <div className="bg-purple-100 p-3 rounded-lg">
            <Database className="w-8 h-8 text-purple-600" />
          </div>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white rounded-xl shadow-sm p-6">
          <div className="flex items-center gap-3">
            <div className="bg-green-100 p-2 rounded-lg">
              <Wallet className="w-5 h-5 text-green-600" />
            </div>
            <div>
              <p className="text-gray-500 text-sm">Balance</p>
              <p className="text-xl font-bold">{state.balance}</p>
            </div>
          </div>
        </div>
        <div className="bg-white rounded-xl shadow-sm p-6">
          <div className="flex items-center gap-3">
            <div className="bg-blue-100 p-2 rounded-lg">
              <ArrowRightLeft className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <p className="text-gray-500 text-sm">Related Transactions</p>
              <p className="text-xl font-bold">{transactions.length}</p>
            </div>
          </div>
        </div>
        <div className="bg-white rounded-xl shadow-sm p-6">
          <div className="flex items-center gap-3">
            <div className="bg-gray-100 p-2 rounded-lg">
              <Clock className="w-5 h-5 text-gray-600" />
            </div>
            <div>
              <p className="text-gray-500 text-sm">Last Updated</p>
              <p className="text-sm font-medium">
                {state.updated_at
                  ? formatDistanceToNow(new Date(state.updated_at), { addSuffix: true })
                  : 'Unknown'}
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* State Data */}
      <div className="bg-white rounded-xl shadow-sm p-6">
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <Database className="w-5 h-5 text-purple-500" />
          State Data
        </h2>
        {state.data && Object.keys(state.data).length > 0 ? (
          <div className="bg-gray-50 p-4 rounded-lg overflow-auto max-h-[500px]">
            <JsonViewer data={state.data} />
          </div>
        ) : (
          <div className="text-gray-500 italic">No state data available</div>
        )}
      </div>

      {/* Related Transactions */}
      <div className="bg-white rounded-xl shadow-sm p-6">
        <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
          <ArrowRightLeft className="w-5 h-5 text-blue-500" />
          Related Transactions
        </h2>
        {transactions.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b text-left text-sm text-gray-500">
                  <th className="pb-3 font-medium">Hash</th>
                  <th className="pb-3 font-medium">Status</th>
                  <th className="pb-3 font-medium">Direction</th>
                  <th className="pb-3 font-medium">From</th>
                  <th className="pb-3 font-medium">To</th>
                  <th className="pb-3 font-medium">Nonce</th>
                  <th className="pb-3 font-medium">Value</th>
                  <th className="pb-3 font-medium">Time</th>
                </tr>
              </thead>
              <tbody>
                {transactions.map((tx) => {
                  const isIncoming = tx.to_address === state.id;
                  const isOutgoing = tx.from_address === state.id;

                  return (
                    <tr key={tx.hash} className="border-b last:border-0 hover:bg-gray-50">
                      <td className="py-3">
                        <div className="flex items-center gap-1">
                          <Link
                            href={`/transactions/${tx.hash}`}
                            className="text-blue-600 hover:underline font-mono text-sm"
                          >
                            {tx.hash.slice(0, 10)}...{tx.hash.slice(-8)}
                          </Link>
                          <CopyButton text={tx.hash} />
                        </div>
                      </td>
                      <td className="py-3">
                        <StatusBadge status={tx.status} />
                      </td>
                      <td className="py-3">
                        <div className="flex gap-1">
                          {isIncoming && (
                            <span className="bg-green-100 text-green-800 text-xs px-2 py-0.5 rounded">
                              IN
                            </span>
                          )}
                          {isOutgoing && (
                            <span className="bg-red-100 text-red-800 text-xs px-2 py-0.5 rounded">
                              OUT
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="py-3 font-mono text-sm">
                        {tx.from_address ? (
                          <div className="flex items-center gap-1">
                            <Link
                              href={`/state/${tx.from_address}`}
                              className="text-blue-600 hover:underline"
                            >
                              {truncateAddress(tx.from_address)}
                            </Link>
                            <CopyButton text={tx.from_address} />
                          </div>
                        ) : (
                          <span className="text-gray-400">-</span>
                        )}
                      </td>
                      <td className="py-3 font-mono text-sm">
                        {tx.to_address ? (
                          <div className="flex items-center gap-1">
                            <Link
                              href={`/state/${tx.to_address}`}
                              className="text-blue-600 hover:underline"
                            >
                              {truncateAddress(tx.to_address)}
                            </Link>
                            <CopyButton text={tx.to_address} />
                          </div>
                        ) : (
                          <span className="text-gray-400">-</span>
                        )}
                      </td>
                      <td className="py-3 text-sm font-mono">
                        {tx.nonce !== null ? tx.nonce : '-'}
                      </td>
                      <td className="py-3 text-sm">
                        {tx.value !== null ? tx.value : '-'}
                      </td>
                      <td className="py-3 text-sm text-gray-500">
                        {tx.created_at
                          ? format(new Date(tx.created_at), 'PPpp')
                          : '-'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="text-gray-500 italic text-center py-8">
            No related transactions found
          </div>
        )}
      </div>
    </div>
  );
}
