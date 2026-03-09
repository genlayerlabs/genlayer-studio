'use client';

import { Transaction } from '@/lib/types';
import { ConsensusViewer } from '@/components/ConsensusViewer';
import { JsonViewer } from '@/components/JsonViewer';

interface ConsensusTabProps {
  transaction: Transaction;
}

export function ConsensusTab({ transaction: tx }: ConsensusTabProps) {
  return (
    <div className="space-y-6">
      <ConsensusViewer
        consensusHistory={tx.consensus_history}
        consensusData={tx.consensus_data}
      />

      {tx.leader_timeout_validators && tx.leader_timeout_validators.length > 0 && (
        <div>
          <h4 className="font-medium text-foreground mb-2">Leader Timeout Validators</h4>
          <div className="bg-red-50 dark:bg-red-950 p-4 rounded-lg border border-red-200 dark:border-red-800">
            <div className="flex flex-wrap gap-2">
              {tx.leader_timeout_validators.map((v, i) => (
                <code key={i} className="bg-card px-2 py-1 rounded text-sm border border-border">
                  {v}
                </code>
              ))}
            </div>
          </div>
        </div>
      )}

      {tx.sim_config && (
        <div>
          <h4 className="font-medium text-foreground mb-2">Simulation Config</h4>
          <div className="bg-muted p-4 rounded-lg">
            <JsonViewer data={tx.sim_config} />
          </div>
        </div>
      )}
    </div>
  );
}
