'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { CurrentState } from '@/lib/types';
import { Search, Loader2, Database, Wallet, Clock } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';

export default function StatePage() {
  const [states, setStates] = useState<CurrentState[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchInput, setSearchInput] = useState('');

  useEffect(() => {
    async function fetchStates() {
      setLoading(true);
      try {
        const params = new URLSearchParams();
        if (searchQuery) params.set('search', searchQuery);

        const res = await fetch(`/api/state?${params.toString()}`);
        if (!res.ok) throw new Error('Failed to fetch states');
        const data = await res.json();
        setStates(data.states);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    }
    fetchStates();
  }, [searchQuery]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setSearchQuery(searchInput);
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-800">Contract State</h1>
        <p className="text-gray-500 mt-1">Browse current contract and account states</p>
      </div>

      {/* Search */}
      <div className="bg-white rounded-xl shadow-sm p-4">
        <form onSubmit={handleSearch}>
          <div className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400 w-5 h-5" />
            <input
              type="text"
              placeholder="Search by address..."
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
            />
          </div>
        </form>
      </div>

      {/* Results */}
      {loading ? (
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
        </div>
      ) : error ? (
        <div className="bg-red-50 border border-red-200 rounded-lg p-6 text-red-700">
          <h2 className="font-bold mb-2">Error loading states</h2>
          <p>{error}</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {states.length === 0 ? (
            <div className="col-span-full bg-white rounded-xl shadow-sm p-8 text-center text-gray-500">
              No states found
            </div>
          ) : (
            states.map((state) => (
              <Link
                key={state.id}
                href={`/state/${state.id}`}
                className="bg-white rounded-xl shadow-sm p-6 hover:shadow-md transition-shadow"
              >
                <div className="flex items-start justify-between mb-4">
                  <div className="bg-purple-100 p-2 rounded-lg">
                    <Database className="w-5 h-5 text-purple-600" />
                  </div>
                  <div className="flex items-center gap-1 text-gray-500 text-sm">
                    <Wallet className="w-4 h-4" />
                    {state.balance}
                  </div>
                </div>
                <div className="font-mono text-sm text-gray-800 truncate mb-2">
                  {state.id}
                </div>
                <div className="flex items-center gap-1 text-gray-400 text-xs">
                  <Clock className="w-3 h-3" />
                  {state.updated_at
                    ? formatDistanceToNow(new Date(state.updated_at), { addSuffix: true })
                    : 'Unknown'}
                </div>
                {state.data && typeof state.data === 'object' && (
                  <div className="mt-3 pt-3 border-t border-gray-100">
                    <div className="text-xs text-gray-500">
                      {Object.keys(state.data).length} fields in state
                    </div>
                  </div>
                )}
              </Link>
            ))
          )}
        </div>
      )}
    </div>
  );
}
