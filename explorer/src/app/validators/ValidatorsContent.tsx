'use client';

import { useState } from 'react';
import { JsonViewer } from '@/components/JsonViewer';
import { Validator } from '@/lib/types';
import { Card, CardContent } from '@/components/ui/card';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Users, Cpu, Coins, Settings } from 'lucide-react';
import { format } from 'date-fns';
import { formatGenValue } from '@/lib/formatters';

export function ValidatorsContent({ validators }: { validators: Validator[] }) {
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const totalStake = validators.reduce((sum, v) => sum + v.stake, 0);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Validators</h1>
        <p className="text-muted-foreground mt-1">Active validators in the network</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card>
          <CardContent className="p-6">
            <div className="flex items-center gap-3">
              <div className="bg-blue-100 dark:bg-blue-950 p-3 rounded-lg">
                <Users className="w-6 h-6 text-blue-600 dark:text-blue-400" />
              </div>
              <div>
                <p className="text-muted-foreground text-sm">Total Validators</p>
                <p className="text-2xl font-bold text-foreground">{validators.length}</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-6">
            <div className="flex items-center gap-3">
              <div className="bg-green-100 dark:bg-green-950 p-3 rounded-lg">
                <Coins className="w-6 h-6 text-green-600 dark:text-green-400" />
              </div>
              <div>
                <p className="text-muted-foreground text-sm">Total Stake</p>
                <p className="text-2xl font-bold text-foreground">{formatGenValue(totalStake)}</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-6">
            <div className="flex items-center gap-3">
              <div className="bg-purple-100 dark:bg-purple-950 p-3 rounded-lg">
                <Cpu className="w-6 h-6 text-purple-600 dark:text-purple-400" />
              </div>
              <div>
                <p className="text-muted-foreground text-sm">Unique Providers</p>
                <p className="text-2xl font-bold text-foreground">
                  {new Set(validators.map(v => v.provider)).size}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="space-y-4">
        {validators.map((validator) => (
          <Collapsible
            key={validator.id}
            open={expandedId === validator.id}
            onOpenChange={(open) => setExpandedId(open ? validator.id : null)}
          >
            <Card>
              <CollapsibleTrigger className="w-full px-6 py-4 flex items-center justify-between hover:bg-accent transition-colors cursor-pointer">
                <div className="flex items-center gap-4">
                  <div className="bg-muted rounded-full w-12 h-12 flex items-center justify-center font-bold text-muted-foreground">
                    #{validator.id}
                  </div>
                  <div className="text-left">
                    <div className="font-medium text-foreground">
                      {validator.provider} / {validator.model}
                    </div>
                    {validator.address && (
                      <div className="text-sm text-muted-foreground font-mono">
                        {validator.address.slice(0, 12)}...{validator.address.slice(-10)}
                      </div>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-6">
                  <div className="text-right">
                    <div className="text-sm text-muted-foreground">Stake</div>
                    <div className="font-medium text-foreground">{formatGenValue(validator.stake)}</div>
                  </div>
                  <div className="text-right">
                    <div className="text-sm text-muted-foreground">Plugin</div>
                    <div className="font-medium text-foreground">{validator.plugin}</div>
                  </div>
                  <Settings className={`w-5 h-5 text-muted-foreground transition-transform ${expandedId === validator.id ? 'rotate-90' : ''}`} />
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
                          <span className="font-mono">{validator.id}</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-muted-foreground">Provider</span>
                          <span>{validator.provider}</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-muted-foreground">Model</span>
                          <span>{validator.model}</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-muted-foreground">Plugin</span>
                          <span>{validator.plugin}</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-muted-foreground">Stake</span>
                          <span>{formatGenValue(validator.stake)}</span>
                        </div>
                        {validator.address && (
                          <div className="flex justify-between">
                            <span className="text-muted-foreground">Address</span>
                            <span className="font-mono text-xs">{validator.address}</span>
                          </div>
                        )}
                        {validator.created_at && (
                          <div className="flex justify-between">
                            <span className="text-muted-foreground">Created</span>
                            <span>{format(new Date(validator.created_at), 'PPpp')}</span>
                          </div>
                        )}
                      </div>
                    </div>
                    <div className="space-y-4">
                      {validator.config && Object.keys(validator.config).length > 0 && (
                        <div>
                          <h4 className="font-medium text-foreground mb-2">Config</h4>
                          <div className="bg-muted p-3 rounded-lg overflow-auto max-h-48">
                            <JsonViewer data={validator.config} initialExpanded={false} />
                          </div>
                        </div>
                      )}
                      {validator.plugin_config && Object.keys(validator.plugin_config).length > 0 && (
                        <div>
                          <h4 className="font-medium text-foreground mb-2">Plugin Config</h4>
                          <div className="bg-muted p-3 rounded-lg overflow-auto max-h-48">
                            <JsonViewer data={validator.plugin_config} initialExpanded={false} />
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

        {validators.length === 0 && (
          <Card>
            <CardContent className="p-8 text-center text-muted-foreground">
              No validators found
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
