'use client';

import { useEffect, useState, useCallback, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { CurrentState } from '@/lib/types';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Search, Loader2, Database, Wallet, Clock, ChevronLeft, ChevronRight } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';

interface StatesResponse {
  states: CurrentState[];
  pagination: {
    page: number;
    limit: number;
    total: number;
    totalPages: number;
  };
}

const PAGE_SIZE_OPTIONS = [12, 24, 48, 96] as const;

function StateContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [data, setData] = useState<StatesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchInput, setSearchInput] = useState(searchParams.get('search') || '');

  const page = parseInt(searchParams.get('page') || '1', 10) || 1;
  const limit = parseInt(searchParams.get('limit') || '24', 10) || 24;
  const search = searchParams.get('search') || '';

  const fetchStates = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      params.set('page', page.toString());
      params.set('limit', limit.toString());
      if (search) params.set('search', search);

      const res = await fetch(`/api/state?${params.toString()}`);
      if (!res.ok) throw new Error('Failed to fetch states');
      const data = await res.json();
      setData(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [page, limit, search]);

  useEffect(() => {
    fetchStates();
  }, [fetchStates]);

  const updateParams = (updates: Record<string, string | null>) => {
    const params = new URLSearchParams(searchParams.toString());
    Object.entries(updates).forEach(([key, value]) => {
      if (value === null || value === '') {
        params.delete(key);
      } else {
        params.set(key, value);
      }
    });
    router.push(`/state?${params.toString()}`);
  };

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    updateParams({ search: searchInput, page: '1' });
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Contracts</h1>
        <p className="text-muted-foreground mt-1">Browse deployed contracts and account states</p>
      </div>

      <Card>
        <CardContent className="p-4">
          <form onSubmit={handleSearch}>
            <div className="relative">
              <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-muted-foreground w-5 h-5" />
              <Input
                type="text"
                placeholder="Search by address..."
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                className="pl-10 h-10"
              />
            </div>
          </form>
        </CardContent>
      </Card>

      {loading ? (
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin text-primary" />
        </div>
      ) : error ? (
        <Card className="border-destructive">
          <CardContent className="p-6">
            <h2 className="font-bold mb-2 text-destructive">Error loading states</h2>
            <p className="text-destructive/80">{error}</p>
          </CardContent>
        </Card>
      ) : data ? (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {data.states.length === 0 ? (
              <div className="col-span-full">
                <Card>
                  <CardContent className="p-8 text-center text-muted-foreground">
                    No states found
                  </CardContent>
                </Card>
              </div>
            ) : (
              data.states.map((state) => (
                <Link key={state.id} href={`/state/${state.id}`}>
                  <Card className="p-6 hover:shadow-md transition-shadow cursor-pointer h-full">
                    <div className="flex items-start justify-between mb-4">
                      <div className="bg-purple-100 dark:bg-purple-950 p-2 rounded-lg">
                        <Database className="w-5 h-5 text-purple-600 dark:text-purple-400" />
                      </div>
                      <div className="flex items-center gap-1 text-muted-foreground text-sm">
                        <Wallet className="w-4 h-4" />
                        {state.balance}
                      </div>
                    </div>
                    <div className="font-mono text-sm text-foreground truncate mb-2">
                      {state.id}
                    </div>
                    <div className="flex items-center gap-1 text-muted-foreground text-xs">
                      <Clock className="w-3 h-3" />
                      {state.updated_at
                        ? formatDistanceToNow(new Date(state.updated_at), { addSuffix: true })
                        : 'Unknown'}
                    </div>
                    {state.data && typeof state.data === 'object' && (
                      <div className="mt-3 pt-3 border-t border-border">
                        <div className="text-xs text-muted-foreground">
                          {Object.keys(state.data).length} fields in state
                        </div>
                      </div>
                    )}
                  </Card>
                </Link>
              ))
            )}
          </div>

          {/* Pagination */}
          {data.pagination.totalPages > 1 && (
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-4">
                <div className="text-sm text-muted-foreground">
                  Showing <span className="font-medium text-foreground">{data.pagination.total === 0 ? 0 : ((page - 1) * limit) + 1}</span> - <span className="font-medium text-foreground">{data.pagination.total === 0 ? 0 : Math.min(page * limit, data.pagination.total)}</span> of <span className="font-medium text-foreground">{data.pagination.total}</span> contracts
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
          )}
        </>
      ) : null}
    </div>
  );
}

export default function StatePage() {
  return (
    <Suspense fallback={
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
      </div>
    }>
      <StateContent />
    </Suspense>
  );
}
