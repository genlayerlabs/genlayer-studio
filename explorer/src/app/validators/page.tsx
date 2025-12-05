'use client';

import { useEffect, useState } from 'react';
import { JsonViewer } from '@/components/JsonViewer';
import { Validator } from '@/lib/types';
import { Loader2, Users, Cpu, Coins, Settings } from 'lucide-react';
import { format } from 'date-fns';

export default function ValidatorsPage() {
  const [validators, setValidators] = useState<Validator[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  useEffect(() => {
    async function fetchValidators() {
      try {
        const res = await fetch('/api/validators');
        if (!res.ok) throw new Error('Failed to fetch validators');
        const data = await res.json();
        setValidators(data.validators);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    }
    fetchValidators();
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-lg p-6 text-red-700">
        <h2 className="font-bold mb-2">Error loading validators</h2>
        <p>{error}</p>
      </div>
    );
  }

  const totalStake = validators.reduce((sum, v) => sum + v.stake, 0);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-800">Validators</h1>
        <p className="text-gray-500 mt-1">Active validators in the network</p>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white rounded-xl shadow-sm p-6">
          <div className="flex items-center gap-3">
            <div className="bg-blue-100 p-3 rounded-lg">
              <Users className="w-6 h-6 text-blue-600" />
            </div>
            <div>
              <p className="text-gray-500 text-sm">Total Validators</p>
              <p className="text-2xl font-bold">{validators.length}</p>
            </div>
          </div>
        </div>
        <div className="bg-white rounded-xl shadow-sm p-6">
          <div className="flex items-center gap-3">
            <div className="bg-green-100 p-3 rounded-lg">
              <Coins className="w-6 h-6 text-green-600" />
            </div>
            <div>
              <p className="text-gray-500 text-sm">Total Stake</p>
              <p className="text-2xl font-bold">{totalStake}</p>
            </div>
          </div>
        </div>
        <div className="bg-white rounded-xl shadow-sm p-6">
          <div className="flex items-center gap-3">
            <div className="bg-purple-100 p-3 rounded-lg">
              <Cpu className="w-6 h-6 text-purple-600" />
            </div>
            <div>
              <p className="text-gray-500 text-sm">Unique Providers</p>
              <p className="text-2xl font-bold">
                {new Set(validators.map(v => v.provider)).size}
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Validators List */}
      <div className="space-y-4">
        {validators.map((validator) => (
          <div key={validator.id} className="bg-white rounded-xl shadow-sm overflow-hidden">
            <button
              onClick={() => setExpandedId(expandedId === validator.id ? null : validator.id)}
              className="w-full px-6 py-4 flex items-center justify-between hover:bg-gray-50 transition-colors"
            >
              <div className="flex items-center gap-4">
                <div className="bg-gray-100 rounded-full w-12 h-12 flex items-center justify-center font-bold text-gray-600">
                  #{validator.id}
                </div>
                <div className="text-left">
                  <div className="font-medium text-gray-800">
                    {validator.provider} / {validator.model}
                  </div>
                  {validator.address && (
                    <div className="text-sm text-gray-500 font-mono">
                      {validator.address.slice(0, 12)}...{validator.address.slice(-10)}
                    </div>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-6">
                <div className="text-right">
                  <div className="text-sm text-gray-500">Stake</div>
                  <div className="font-medium text-gray-800">{validator.stake}</div>
                </div>
                <div className="text-right">
                  <div className="text-sm text-gray-500">Plugin</div>
                  <div className="font-medium text-gray-800">{validator.plugin}</div>
                </div>
                <Settings className={`w-5 h-5 text-gray-400 transition-transform ${expandedId === validator.id ? 'rotate-90' : ''}`} />
              </div>
            </button>

            {expandedId === validator.id && (
              <div className="px-6 pb-6 border-t border-gray-100">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-4">
                  <div>
                    <h4 className="font-medium text-gray-700 mb-2">Details</h4>
                    <div className="space-y-2 text-sm">
                      <div className="flex justify-between">
                        <span className="text-gray-500">ID</span>
                        <span className="font-mono">{validator.id}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-500">Provider</span>
                        <span>{validator.provider}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-500">Model</span>
                        <span>{validator.model}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-500">Plugin</span>
                        <span>{validator.plugin}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-500">Stake</span>
                        <span>{validator.stake}</span>
                      </div>
                      {validator.address && (
                        <div className="flex justify-between">
                          <span className="text-gray-500">Address</span>
                          <span className="font-mono text-xs">{validator.address}</span>
                        </div>
                      )}
                      {validator.created_at && (
                        <div className="flex justify-between">
                          <span className="text-gray-500">Created</span>
                          <span>{format(new Date(validator.created_at), 'PPpp')}</span>
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="space-y-4">
                    {validator.config && Object.keys(validator.config).length > 0 && (
                      <div>
                        <h4 className="font-medium text-gray-700 mb-2">Config</h4>
                        <div className="bg-gray-50 p-3 rounded-lg overflow-auto max-h-48">
                          <JsonViewer data={validator.config} initialExpanded={false} />
                        </div>
                      </div>
                    )}
                    {validator.plugin_config && Object.keys(validator.plugin_config).length > 0 && (
                      <div>
                        <h4 className="font-medium text-gray-700 mb-2">Plugin Config</h4>
                        <div className="bg-gray-50 p-3 rounded-lg overflow-auto max-h-48">
                          <JsonViewer data={validator.plugin_config} initialExpanded={false} />
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}
          </div>
        ))}

        {validators.length === 0 && (
          <div className="bg-white rounded-xl shadow-sm p-8 text-center text-gray-500">
            No validators found
          </div>
        )}
      </div>
    </div>
  );
}
