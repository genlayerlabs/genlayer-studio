'use client';

import { useEffect, useState, useCallback, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { usePagination } from '@/hooks/usePagination';
import { Transaction } from '@/lib/types';
import { TransactionTable } from '@/components/TransactionTable';
import { PAGE_SIZE_OPTIONS, TRANSACTION_TABS } from '@/lib/constants';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Search, ChevronLeft, ChevronRight, Loader2, X } from 'lucide-react';
import { DateTimePicker } from '@/components/DateTimePicker';

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
  const searchParams = useSearchParams();
  const { page, limit, updateParams } = usePagination(searchParams, 20);
  const [data, setData] = useState<TransactionsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState(searchParams.get('search') || '');
  const [highlightedHashes, setHighlightedHashes] = useState<Set<string>>(new Set());

  const tab = searchParams.get('tab') || 'all';
  const search = searchParams.get('search') || '';
  const fromDate = searchParams.get('from_date') || '';
  const toDate = searchParams.get('to_date') || '';

  // Derive comma-separated statuses from the active tab
  const activeTab = TRANSACTION_TABS.find(t => t.id === tab) || TRANSACTION_TABS[0];
  const statusFilter = activeTab.statuses ? activeTab.statuses.join(',') : '';

  const fetchTransactions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      params.set('page', page.toString());
      params.set('limit', limit.toString());
      if (statusFilter) params.set('status', statusFilter);
      if (search) params.set('search', search);
      if (fromDate) params.set('from_date', fromDate);
      if (toDate) params.set('to_date', toDate);

      const res = await fetch(`/api/transactions?${params.toString()}`);
      if (!res.ok) throw new Error('Failed to fetch transactions');
      const data = await res.json();
      setData(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [page, limit, statusFilter, search, fromDate, toDate]);

  useEffect(() => {
    fetchTransactions();
  }, [fetchTransactions]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    updateParams({ search: searchQuery, page: '1' });
  };

  const highlightParent = (parentHash: string | null) => {
    if (parentHash) {
      setHighlightedHashes(new Set([parentHash]));
    }
  };

  const highlightChildren = (parentHash: string) => {
    if (data) {
      const childHashes = data.transactions
        .filter(tx => tx.triggered_by_hash === parentHash)
        .map(tx => tx.hash);
      setHighlightedHashes(new Set(childHashes));
    }
  };

  const clearHighlights = () => {
    setHighlightedHashes(new Set());
  };

  return (
    <div className="space-y-6 animate-fade-in">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Transactions</h1>
        <p className="text-muted-foreground mt-1">Browse and search all transactions</p>
      </div>

      <Tabs value={tab} onValueChange={(value) => updateParams({ tab: value === 'all' ? null : value, page: '1' })}>
        <TabsList>
          {TRANSACTION_TABS.map((t) => (
            <TabsTrigger key={t.id} value={t.id}>{t.label}</TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      <Card>
        <CardContent className="p-4">
          <div className="flex flex-wrap gap-4 items-center">
            <form onSubmit={handleSearch} className="flex-1 min-w-[300px]">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-muted-foreground w-5 h-5" />
                <Input
                  type="text"
                  placeholder="Search by hash, from, or to address..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="pl-10 h-10"
                />
              </div>
            </form>

            <div className="flex items-center gap-2">
              <DateTimePicker
                value={fromDate}
                onChange={(v) => updateParams({ from_date: v || null, page: '1' })}
                placeholder="From"
              />
              <span className="text-muted-foreground text-sm">to</span>
              <DateTimePicker
                value={toDate}
                onChange={(v) => updateParams({ to_date: v || null, page: '1' })}
                placeholder="To"
              />
            </div>

            {(tab !== 'all' || search || fromDate || toDate) && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setSearchQuery('');
                  updateParams({ tab: null, search: null, from_date: null, to_date: null, page: '1' });
                }}
              >
                <X className="w-4 h-4 mr-1" />
                Clear filters
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {loading ? (
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin text-primary" />
        </div>
      ) : error ? (
        <Card className="border-destructive">
          <CardContent className="p-6">
            <h2 className="font-bold mb-2 text-destructive">Error loading transactions</h2>
            <p className="text-destructive/80">{error}</p>
          </CardContent>
        </Card>
      ) : data ? (
        <>
          <Card className="overflow-hidden">
            <TransactionTable
              transactions={data.transactions}
              showRelations={true}
              onHighlightParent={highlightParent}
              onHighlightChildren={highlightChildren}
              onClearHighlights={clearHighlights}
              highlightedHashes={highlightedHashes}
            />
          </Card>

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <div className="text-sm text-muted-foreground">
                Showing <span className="font-medium text-foreground">{data.pagination.total === 0 ? 0 : ((page - 1) * limit) + 1}</span> - <span className="font-medium text-foreground">{data.pagination.total === 0 ? 0 : Math.min(page * limit, data.pagination.total)}</span> of <span className="font-medium text-foreground">{data.pagination.total}</span> transactions
              </div>
              <div className="flex items-center gap-2">
                <span className="text-sm text-muted-foreground">Page size:</span>
                <Select
                  value={limit.toString()}
                  onValueChange={(value) => updateParams({ limit: value, page: '1' })}
                >
                  <SelectTrigger className="w-[70px] h-8">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {PAGE_SIZE_OPTIONS.map((size) => (
                      <SelectItem key={size} value={size.toString()}>{size}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => updateParams({ page: (page - 1).toString() })}
                disabled={page <= 1}
              >
                <ChevronLeft className="w-4 h-4 mr-1" />
                Previous
              </Button>
              <span className="px-4 py-2 text-sm text-muted-foreground">
                Page <span className="font-medium text-foreground">{page}</span> of <span className="font-medium text-foreground">{data.pagination.totalPages}</span>
              </span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => updateParams({ page: (page + 1).toString() })}
                disabled={page >= data.pagination.totalPages}
              >
                Next
                <ChevronRight className="w-4 h-4 ml-1" />
              </Button>
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
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
      </div>
    }>
      <TransactionsContent />
    </Suspense>
  );
}
