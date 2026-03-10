'use client';

import { useState } from 'react';
import { JsonViewer } from './JsonViewer';
import { ConsensusRound, LegacyConsensusEntry } from './ConsensusRound';
import { ConsensusHistoryData } from '@/lib/types';
import { isNewConsensusFormat, isLegacyConsensusFormat } from '@/lib/consensusUtils';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';

interface ConsensusViewerProps {
  consensusHistory: ConsensusHistoryData | null;
  consensusData: Record<string, unknown> | null;
}

export function ConsensusViewer({ consensusHistory, consensusData }: ConsensusViewerProps) {
  const [showRawData, setShowRawData] = useState(false);

  if (!consensusHistory && !consensusData) {
    return (
      <div className="text-muted-foreground italic p-4 bg-muted rounded-lg">
        No consensus data available
      </div>
    );
  }

  const isNewFormat = consensusHistory && isNewConsensusFormat(consensusHistory);
  const legacyEntries = isLegacyConsensusFormat(consensusHistory)
    ? (consensusHistory as LegacyConsensusEntry[])
    : null;

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button variant="ghost" size="sm" onClick={() => setShowRawData(!showRawData)}>
          {showRawData ? 'Show Formatted' : 'Show Raw JSON'}
        </Button>
      </div>

      {showRawData ? (
        <div className="space-y-4">
          {consensusHistory && (
            <div>
              <h4 className="font-medium text-foreground mb-2">Consensus History</h4>
              <div className="bg-muted p-4 rounded-lg overflow-auto max-h-96">
                <JsonViewer data={consensusHistory} />
              </div>
            </div>
          )}
          {consensusData && (
            <div>
              <h4 className="font-medium text-foreground mb-2">Consensus Data</h4>
              <div className="bg-muted p-4 rounded-lg overflow-auto max-h-96">
                <JsonViewer data={consensusData} />
              </div>
            </div>
          )}
        </div>
      ) : (
        <>
          {isNewFormat && (
            <Card className="border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-950">
              <CardContent className="p-4">
                <p className="text-blue-700 dark:text-blue-400 text-sm">
                  This transaction uses the new consensus format with detailed monitoring timestamps.
                  View the <span className="font-semibold">Monitoring</span> tab for a detailed timeline visualization.
                </p>
                <div className="mt-3">
                  <h4 className="font-medium text-foreground mb-2">Raw Consensus Data</h4>
                  <div className="bg-card p-4 rounded-lg overflow-auto max-h-96">
                    <JsonViewer data={consensusHistory} />
                  </div>
                </div>
              </CardContent>
            </Card>
          )}

          {legacyEntries && legacyEntries.length > 0 && (
            <div>
              <h4 className="font-medium text-foreground mb-3">
                Consensus Rounds ({legacyEntries.length})
              </h4>
              <div className="space-y-3">
                {legacyEntries.map((entry, idx) => (
                  <ConsensusRound key={idx} entry={entry} index={idx} />
                ))}
              </div>
            </div>
          )}

          {consensusData && Object.keys(consensusData).length > 0 && (
            <div>
              <h4 className="font-medium text-foreground mb-2">Additional Consensus Data</h4>
              <div className="bg-muted p-4 rounded-lg">
                <JsonViewer data={consensusData} initialExpanded={true} />
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
