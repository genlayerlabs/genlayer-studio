'use client';

import { useEffect, useState, useCallback, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { Transaction } from '@/lib/types';
import { TransactionTable } from '@/components/TransactionTable';
import { TRANSACTION_STATUS_OPTIONS, PAGE_SIZE_OPTIONS } from '@/lib/constants';
import { Search, ChevronLeft, ChevronRight, Loader2, Filter, X } from 'lucide-react';

interface TransactionsResponse {
  transactions: Transaction[];
  pagination: {
    page: number;
    limit: number;
    total: number;
    totalPages: number;
  };
}

function TransactionsContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [data, setData] = useState<TransactionsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState(searchParams.get('search') || '');
  const [highlightedHashes, setHighlightedHashes] = useState<Set<string>>(new Set());

  const page = parseInt(searchParams.get('page') || '1');
  const limit = parseInt(searchParams.get('limit') || '20');
  const status = searchParams.get('status') || '';
  const search = searchParams.get('search') || '';

  const fetchTransactions = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      params.set('page', page.toString());
      params.set('limit', limit.toString());
      if (status) params.set('status', status);
      if (search) params.set('search', search);

      const res = await fetch(`/api/transactions?${params.toString()}`);
      if (!res.ok) throw new Error('Failed to fetch transactions');
      const data = await res.json();
      setData(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [page, limit, status, search]);

  useEffect(() => {
    fetchTransactions();
  }, [fetchTransactions]);

  const updateParams = (updates: Record<string, string | null>) => {
    const params = new URLSearchParams(searchParams.toString());
    Object.entries(updates).forEach(([key, value]) => {
      if (value === null || value === '') {
        params.delete(key);
      } else {
        params.set(key, value);
      }
    });
    router.push(`/transactions?${params.toString()}`);
  };

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    updateParams({ search: searchQuery, page: '1' });
  };

  // Highlight parent transaction when hovering over "Parent" badge
  const highlightParent = (parentHash: string | null) => {
    if (parentHash) {
      setHighlightedHashes(new Set([parentHash]));
    }
  };

  // Highlight child transactions when hovering over triggered count badge
  const highlightChildren = (parentHash: string) => {
    if (data) {
      const childHashes = data.transactions
        .filter(tx => tx.triggered_by_hash === parentHash)
        .map(tx => tx.hash);
      setHighlightedHashes(new Set(childHashes));
    }
  };

  // Clear all highlights
  const clearHighlights = () => {
    setHighlightedHashes(new Set());
  };

  return (
    <div className="space-y-6 animate-fade-in">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Transactions</h1>
        <p className="text-slate-600 mt-1">Browse and search all transactions</p>
      </div>

      {/* Filters */}
      <div className="bg-white rounded-xl border border-slate-200 p-4">
        <div className="flex flex-wrap gap-4 items-center">
          {/* Search */}
          <form onSubmit={handleSearch} className="flex-1 min-w-[300px]">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-slate-400 w-5 h-5" />
              <input
                type="text"
                placeholder="Search by hash, from, or to address..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-10 pr-4 py-2.5 border border-slate-200 rounded-xl focus:ring-2 focus:ring-blue-500 focus:border-blue-500 text-slate-800 placeholder:text-slate-400"
              />
            </div>
          </form>

          {/* Status Filter */}
          <div className="flex items-center gap-2">
            <Filter className="w-5 h-5 text-slate-400" />
            <select
              value={status}
              onChange={(e) => updateParams({ status: e.target.value, page: '1' })}
              className="border border-slate-200 rounded-xl px-3 py-2.5 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 text-slate-700"
            >
              <option value="">All Statuses</option>
              {TRANSACTION_STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>
              ))}
            </select>
          </div>

          {/* Clear Filters */}
          {(status || search) && (
            <button
              onClick={() => {
                setSearchQuery('');
                updateParams({ status: null, search: null, page: '1' });
              }}
              className="flex items-center gap-1 text-slate-500 hover:text-slate-700 font-medium"
            >
              <X className="w-4 h-4" />
              Clear filters
            </button>
          )}
        </div>
      </div>

      {/* Results */}
      {loading ? (
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
        </div>
      ) : error ? (
        <div className="bg-red-50 border border-red-200 rounded-xl p-6 text-red-800">
          <h2 className="font-bold mb-2">Error loading transactions</h2>
          <p className="text-red-700">{error}</p>
        </div>
      ) : data ? (
        <>
          <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
            <TransactionTable
              transactions={data.transactions}
              showRelations={true}
              onHighlightParent={highlightParent}
              onHighlightChildren={highlightChildren}
              onClearHighlights={clearHighlights}
              highlightedHashes={highlightedHashes}
            />
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <div className="text-sm text-slate-600">
                Showing <span className="font-medium text-slate-800">{((page - 1) * limit) + 1}</span> - <span className="font-medium text-slate-800">{Math.min(page * limit, data.pagination.total)}</span> of <span className="font-medium text-slate-800">{data.pagination.total}</span> transactions
              </div>
              <div className="flex items-center gap-2">
                <span className="text-sm text-slate-600">Page size:</span>
                <select
                  value={limit}
                  onChange={(e) => updateParams({ limit: e.target.value, page: '1' })}
                  className="border border-slate-200 rounded-lg px-2 py-1.5 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 text-slate-700"
                >
                  {PAGE_SIZE_OPTIONS.map((size) => (
                    <option key={size} value={size}>{size}</option>
                  ))}
                </select>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => updateParams({ page: (page - 1).toString() })}
                disabled={page <= 1}
                className="flex items-center gap-1 px-4 py-2 border border-slate-200 rounded-xl hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed text-slate-700 font-medium transition-colors"
              >
                <ChevronLeft className="w-4 h-4" />
                Previous
              </button>
              <span className="px-4 py-2 text-sm text-slate-600">
                Page <span className="font-medium text-slate-800">{page}</span> of <span className="font-medium text-slate-800">{data.pagination.totalPages}</span>
              </span>
              <button
                onClick={() => updateParams({ page: (page + 1).toString() })}
                disabled={page >= data.pagination.totalPages}
                className="flex items-center gap-1 px-4 py-2 border border-slate-200 rounded-xl hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed text-slate-700 font-medium transition-colors"
              >
                Next
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}

export default function TransactionsPage() {
  return (
    <Suspense fallback={
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
      </div>
    }>
      <TransactionsContent />
    </Suspense>
  );
}
