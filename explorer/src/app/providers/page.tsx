'use client';

import { useEffect, useState } from 'react';
import { JsonViewer } from '@/components/JsonViewer';
import { LLMProvider } from '@/lib/types';
import { Loader2, Cpu, Star, Settings, Clock } from 'lucide-react';
import { format } from 'date-fns';

export default function ProvidersPage() {
  const [providers, setProviders] = useState<LLMProvider[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  useEffect(() => {
    async function fetchProviders() {
      try {
        const res = await fetch('/api/providers');
        if (!res.ok) throw new Error('Failed to fetch providers');
        const data = await res.json();
        setProviders(data.providers);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    }
    fetchProviders();
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
        <h2 className="font-bold mb-2">Error loading providers</h2>
        <p>{error}</p>
      </div>
    );
  }

  // Group providers by provider name
  const groupedProviders = providers.reduce((acc, provider) => {
    if (!acc[provider.provider]) {
      acc[provider.provider] = [];
    }
    acc[provider.provider].push(provider);
    return acc;
  }, {} as Record<string, LLMProvider[]>);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-800">LLM Providers</h1>
        <p className="text-gray-500 mt-1">Configured language model providers and their settings</p>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white rounded-xl shadow-sm p-6">
          <div className="flex items-center gap-3">
            <div className="bg-blue-100 p-3 rounded-lg">
              <Cpu className="w-6 h-6 text-blue-600" />
            </div>
            <div>
              <p className="text-gray-500 text-sm">Total Providers</p>
              <p className="text-2xl font-bold">{providers.length}</p>
            </div>
          </div>
        </div>
        <div className="bg-white rounded-xl shadow-sm p-6">
          <div className="flex items-center gap-3">
            <div className="bg-purple-100 p-3 rounded-lg">
              <Settings className="w-6 h-6 text-purple-600" />
            </div>
            <div>
              <p className="text-gray-500 text-sm">Provider Types</p>
              <p className="text-2xl font-bold">{Object.keys(groupedProviders).length}</p>
            </div>
          </div>
        </div>
        <div className="bg-white rounded-xl shadow-sm p-6">
          <div className="flex items-center gap-3">
            <div className="bg-yellow-100 p-3 rounded-lg">
              <Star className="w-6 h-6 text-yellow-600" />
            </div>
            <div>
              <p className="text-gray-500 text-sm">Default Providers</p>
              <p className="text-2xl font-bold">{providers.filter(p => p.is_default).length}</p>
            </div>
          </div>
        </div>
      </div>

      {/* Providers by Group */}
      {Object.entries(groupedProviders).map(([providerName, providerList]) => (
        <div key={providerName} className="space-y-3">
          <h2 className="text-lg font-semibold text-gray-700 flex items-center gap-2">
            <Cpu className="w-5 h-5" />
            {providerName}
            <span className="text-sm font-normal text-gray-400">
              ({providerList.length} model{providerList.length !== 1 ? 's' : ''})
            </span>
          </h2>

          <div className="space-y-2">
            {providerList.map((provider) => (
              <div key={provider.id} className="bg-white rounded-xl shadow-sm overflow-hidden">
                <button
                  onClick={() => setExpandedId(expandedId === provider.id ? null : provider.id)}
                  className="w-full px-6 py-4 flex items-center justify-between hover:bg-gray-50 transition-colors"
                >
                  <div className="flex items-center gap-4">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-gray-800">{provider.model}</span>
                      {provider.is_default && (
                        <span className="bg-yellow-100 text-yellow-800 text-xs px-2 py-0.5 rounded-full flex items-center gap-1">
                          <Star className="w-3 h-3" />
                          Default
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-4 text-sm text-gray-500">
                    <span className="bg-gray-100 px-2 py-1 rounded">{provider.plugin}</span>
                    <Settings className={`w-5 h-5 text-gray-400 transition-transform ${expandedId === provider.id ? 'rotate-90' : ''}`} />
                  </div>
                </button>

                {expandedId === provider.id && (
                  <div className="px-6 pb-6 border-t border-gray-100">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-4">
                      <div>
                        <h4 className="font-medium text-gray-700 mb-2">Details</h4>
                        <div className="space-y-2 text-sm">
                          <div className="flex justify-between">
                            <span className="text-gray-500">ID</span>
                            <span className="font-mono">{provider.id}</span>
                          </div>
                          <div className="flex justify-between">
                            <span className="text-gray-500">Provider</span>
                            <span>{provider.provider}</span>
                          </div>
                          <div className="flex justify-between">
                            <span className="text-gray-500">Model</span>
                            <span>{provider.model}</span>
                          </div>
                          <div className="flex justify-between">
                            <span className="text-gray-500">Plugin</span>
                            <span>{provider.plugin}</span>
                          </div>
                          <div className="flex justify-between">
                            <span className="text-gray-500">Default</span>
                            <span>{provider.is_default ? 'Yes' : 'No'}</span>
                          </div>
                          <div className="flex justify-between items-center">
                            <span className="text-gray-500">Created</span>
                            <span className="flex items-center gap-1">
                              <Clock className="w-3 h-3" />
                              {format(new Date(provider.created_at), 'PPpp')}
                            </span>
                          </div>
                          <div className="flex justify-between items-center">
                            <span className="text-gray-500">Updated</span>
                            <span className="flex items-center gap-1">
                              <Clock className="w-3 h-3" />
                              {format(new Date(provider.updated_at), 'PPpp')}
                            </span>
                          </div>
                        </div>
                      </div>
                      <div className="space-y-4">
                        {provider.config && (typeof provider.config === 'object' ? Object.keys(provider.config).length > 0 : provider.config) && (
                          <div>
                            <h4 className="font-medium text-gray-700 mb-2">Config</h4>
                            <div className="bg-gray-50 p-3 rounded-lg overflow-auto max-h-48">
                              <JsonViewer data={provider.config} initialExpanded={false} />
                            </div>
                          </div>
                        )}
                        {provider.plugin_config && Object.keys(provider.plugin_config).length > 0 && (
                          <div>
                            <h4 className="font-medium text-gray-700 mb-2">Plugin Config</h4>
                            <div className="bg-gray-50 p-3 rounded-lg overflow-auto max-h-48">
                              <JsonViewer data={provider.plugin_config} initialExpanded={false} />
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}

      {providers.length === 0 && (
        <div className="bg-white rounded-xl shadow-sm p-8 text-center text-gray-500">
          No LLM providers configured
        </div>
      )}
    </div>
  );
}
