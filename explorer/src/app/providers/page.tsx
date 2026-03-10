'use client';

import { useEffect, useState } from 'react';
import { JsonViewer } from '@/components/JsonViewer';
import { LLMProvider } from '@/lib/types';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
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
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
      </div>
    );
  }

  if (error) {
    return (
      <Card className="border-destructive">
        <CardContent className="p-6">
          <h2 className="font-bold mb-2 text-destructive">Error loading providers</h2>
          <p className="text-destructive/80">{error}</p>
        </CardContent>
      </Card>
    );
  }

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
        <h1 className="text-2xl font-bold text-foreground">LLM Providers</h1>
        <p className="text-muted-foreground mt-1">Configured language model providers and their settings</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card>
          <CardContent className="p-6">
            <div className="flex items-center gap-3">
              <div className="bg-blue-100 dark:bg-blue-950 p-3 rounded-lg">
                <Cpu className="w-6 h-6 text-blue-600 dark:text-blue-400" />
              </div>
              <div>
                <p className="text-muted-foreground text-sm">Total Providers</p>
                <p className="text-2xl font-bold text-foreground">{providers.length}</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-6">
            <div className="flex items-center gap-3">
              <div className="bg-purple-100 dark:bg-purple-950 p-3 rounded-lg">
                <Settings className="w-6 h-6 text-purple-600 dark:text-purple-400" />
              </div>
              <div>
                <p className="text-muted-foreground text-sm">Provider Types</p>
                <p className="text-2xl font-bold text-foreground">{Object.keys(groupedProviders).length}</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-6">
            <div className="flex items-center gap-3">
              <div className="bg-yellow-100 dark:bg-yellow-950 p-3 rounded-lg">
                <Star className="w-6 h-6 text-yellow-600 dark:text-yellow-400" />
              </div>
              <div>
                <p className="text-muted-foreground text-sm">Default Providers</p>
                <p className="text-2xl font-bold text-foreground">{providers.filter(p => p.is_default).length}</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {Object.entries(groupedProviders).map(([providerName, providerList]) => (
        <div key={providerName} className="space-y-3">
          <h2 className="text-lg font-semibold text-foreground flex items-center gap-2">
            <Cpu className="w-5 h-5" />
            {providerName}
            <span className="text-sm font-normal text-muted-foreground">
              ({providerList.length} model{providerList.length !== 1 ? 's' : ''})
            </span>
          </h2>

          <div className="space-y-2">
            {providerList.map((provider) => (
              <Collapsible
                key={provider.id}
                open={expandedId === provider.id}
                onOpenChange={(open) => setExpandedId(open ? provider.id : null)}
              >
                <Card>
                  <CollapsibleTrigger className="w-full px-6 py-4 flex items-center justify-between hover:bg-accent transition-colors cursor-pointer">
                    <div className="flex items-center gap-4">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-foreground">{provider.model}</span>
                        {provider.is_default && (
                          <Badge className="bg-yellow-100 dark:bg-yellow-900 text-yellow-800 dark:text-yellow-300 border-yellow-200 dark:border-yellow-800">
                            <Star className="w-3 h-3 mr-1" />
                            Default
                          </Badge>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-4 text-sm text-muted-foreground">
                      <Badge variant="secondary">{provider.plugin}</Badge>
                      <Settings className={`w-5 h-5 text-muted-foreground transition-transform ${expandedId === provider.id ? 'rotate-90' : ''}`} />
                    </div>
                  </CollapsibleTrigger>

                  <CollapsibleContent>
                    <div className="px-6 pb-6 border-t border-border">
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-4">
                        <div>
                          <h4 className="font-medium text-foreground mb-2">Details</h4>
                          <div className="space-y-2 text-sm">
                            <div className="flex justify-between">
                              <span className="text-muted-foreground">ID</span>
                              <span className="font-mono">{provider.id}</span>
                            </div>
                            <div className="flex justify-between">
                              <span className="text-muted-foreground">Provider</span>
                              <span>{provider.provider}</span>
                            </div>
                            <div className="flex justify-between">
                              <span className="text-muted-foreground">Model</span>
                              <span>{provider.model}</span>
                            </div>
                            <div className="flex justify-between">
                              <span className="text-muted-foreground">Plugin</span>
                              <span>{provider.plugin}</span>
                            </div>
                            <div className="flex justify-between">
                              <span className="text-muted-foreground">Default</span>
                              <span>{provider.is_default ? 'Yes' : 'No'}</span>
                            </div>
                            <div className="flex justify-between items-center">
                              <span className="text-muted-foreground">Created</span>
                              <span className="flex items-center gap-1">
                                <Clock className="w-3 h-3" />
                                {format(new Date(provider.created_at), 'PPpp')}
                              </span>
                            </div>
                            <div className="flex justify-between items-center">
                              <span className="text-muted-foreground">Updated</span>
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
                              <h4 className="font-medium text-foreground mb-2">Config</h4>
                              <div className="bg-muted p-3 rounded-lg overflow-auto max-h-48">
                                <JsonViewer data={provider.config} initialExpanded={false} />
                              </div>
                            </div>
                          )}
                          {provider.plugin_config && Object.keys(provider.plugin_config).length > 0 && (
                            <div>
                              <h4 className="font-medium text-foreground mb-2">Plugin Config</h4>
                              <div className="bg-muted p-3 rounded-lg overflow-auto max-h-48">
                                <JsonViewer data={provider.plugin_config} initialExpanded={false} />
                              </div>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  </CollapsibleContent>
                </Card>
              </Collapsible>
            ))}
          </div>
        </div>
      ))}

      {providers.length === 0 && (
        <Card>
          <CardContent className="p-8 text-center text-muted-foreground">
            No LLM providers configured
          </CardContent>
        </Card>
      )}
    </div>
  );
}
