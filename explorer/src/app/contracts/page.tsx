'use client';

import { useEffect, useState, useCallback, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { CurrentState } from '@/lib/types';
import { CopyButton } from '@/components/CopyButton';
import { Card, CardContent } from '@/components/ui/card';
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Loader2, ChevronLeft, ChevronRight, ArrowUpDown, ArrowUp, ArrowDown } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { formatGenValue, truncateAddress } from '@/lib/formatters';

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

  const page = parseInt(searchParams.get('page') || '1', 10) || 1;
  const limit = parseInt(searchParams.get('limit') || '24', 10) || 24;
  const sortBy = searchParams.get('sort_by') || '';
  const sortOrder = searchParams.get('sort_order') || 'desc';

  const fetchStates = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      params.set('page', page.toString());
      params.set('limit', limit.toString());
      if (sortBy) params.set('sort_by', sortBy);
      if (sortBy) params.set('sort_order', sortOrder);

      const res = await fetch(`/api/contracts?${params.toString()}`);
      if (!res.ok) throw new Error('Failed to fetch states');
      const data = await res.json();
      setData(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [page, limit, sortBy, sortOrder]);

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
    router.push(`/contracts?${params.toString()}`);
  };

  const toggleSort = (column: string) => {
    if (sortBy === column) {
      updateParams({ sort_order: sortOrder === 'desc' ? 'asc' : 'desc', page: '1' });
    } else {
      updateParams({ sort_by: column, sort_order: 'desc', page: '1' });
    }
  };

  const SortIcon = ({ column }: { column: string }) => {
    if (sortBy !== column) return <ArrowUpDown className="w-3.5 h-3.5 text-muted-foreground/50" />;
    return sortOrder === 'asc'
      ? <ArrowUp className="w-3.5 h-3.5 text-foreground" />
      : <ArrowDown className="w-3.5 h-3.5 text-foreground" />;
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Contracts</h1>
        <p className="text-muted-foreground mt-1">Browse deployed contracts and account states</p>
      </div>

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
          <Card>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Address</TableHead>
                  <TableHead>Balance</TableHead>
                  <TableHead>
                    <button onClick={() => toggleSort('tx_count')} className="flex items-center gap-1 hover:text-foreground transition-colors cursor-pointer">
                      Transactions <SortIcon column="tx_count" />
                    </button>
                  </TableHead>
                  <TableHead>State Fields</TableHead>
                  <TableHead>
                    <button onClick={() => toggleSort('created_at')} className="flex items-center gap-1 hover:text-foreground transition-colors cursor-pointer">
                      Created <SortIcon column="created_at" />
                    </button>
                  </TableHead>
                  <TableHead>
                    <button onClick={() => toggleSort('updated_at')} className="flex items-center gap-1 hover:text-foreground transition-colors cursor-pointer">
                      Last Updated <SortIcon column="updated_at" />
                    </button>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.states.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={6} className="text-center text-muted-foreground py-8">
                      No contracts found
                    </TableCell>
                  </TableRow>
                ) : (
                  data.states.map((state) => (
                    <TableRow key={state.id} className="cursor-pointer hover:bg-accent/50">
                      <TableCell className="font-mono text-sm">
                        <div className="flex items-center gap-1">
                          <Link href={`/contracts/${state.id}`} className="text-primary hover:underline">
                            {truncateAddress(state.id, 10, 8)}
                          </Link>
                          <CopyButton text={state.id} />
                        </div>
                      </TableCell>
                      <TableCell className="text-sm">
                        {formatGenValue(state.balance)}
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {state.tx_count ?? '-'}
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {state.data && typeof state.data === 'object'
                          ? Object.keys(state.data).length
                          : '-'}
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {state.created_at
                          ? formatDistanceToNow(new Date(state.created_at), { addSuffix: true })
                          : '-'}
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {state.updated_at
                          ? formatDistanceToNow(new Date(state.updated_at), { addSuffix: true })
                          : 'Unknown'}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </Card>

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
