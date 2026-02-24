'use client';

import { useState } from 'react';
import { JsonViewer } from './JsonViewer';
import { ConsensusRound, LegacyConsensusEntry } from './ConsensusRound';
import { ConsensusHistoryData } from '@/lib/types';
import { isNewConsensusFormat, isLegacyConsensusFormat } from '@/lib/consensusUtils';

interface ConsensusViewerProps {
  consensusHistory: ConsensusHistoryData | null;
  consensusData: Record<string, unknown> | null;
}

export function ConsensusViewer({ consensusHistory, consensusData }: ConsensusViewerProps) {
  const [showRawData, setShowRawData] = useState(false);

  if (!consensusHistory && !consensusData) {
    return (
      <div className="text-gray-500 italic p-4 bg-gray-50 rounded">
        No consensus data available
      </div>
    );
  }

  // Determine the format and get the entries
  const isNewFormat = consensusHistory && isNewConsensusFormat(consensusHistory);
  const legacyEntries = isLegacyConsensusFormat(consensusHistory)
    ? (consensusHistory as LegacyConsensusEntry[])
    : null;

  return (
    <div className="space-y-4">
      {/* Toggle for raw data */}
      <div className="flex justify-end">
        <button
          onClick={() => setShowRawData(!showRawData)}
          className="text-sm text-gray-600 hover:text-gray-800 underline"
        >
          {showRawData ? 'Show Formatted' : 'Show Raw JSON'}
        </button>
      </div>

      {showRawData ? (
        <div className="space-y-4">
          {consensusHistory && (
            <div>
              <h4 className="font-medium text-gray-700 mb-2">Consensus History</h4>
              <div className="bg-gray-50 p-4 rounded overflow-auto max-h-96">
                <JsonViewer data={consensusHistory} />
              </div>
            </div>
          )}
          {consensusData && (
            <div>
              <h4 className="font-medium text-gray-700 mb-2">Consensus Data</h4>
              <div className="bg-gray-50 p-4 rounded overflow-auto max-h-96">
                <JsonViewer data={consensusData} />
              </div>
            </div>
          )}
        </div>
      ) : (
        <>
          {/* New format - show message to use Monitoring tab */}
          {isNewFormat && (
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
              <p className="text-blue-700 text-sm">
                This transaction uses the new consensus format with detailed monitoring timestamps.
                View the <span className="font-semibold">Monitoring</span> tab for a detailed timeline visualization.
              </p>
              <div className="mt-3">
                <h4 className="font-medium text-gray-700 mb-2">Raw Consensus Data</h4>
                <div className="bg-white p-4 rounded overflow-auto max-h-96">
                  <JsonViewer data={consensusHistory} />
                </div>
              </div>
            </div>
          )}

          {/* Legacy format - Consensus History Rounds */}
          {legacyEntries && legacyEntries.length > 0 && (
            <div>
              <h4 className="font-medium text-gray-700 mb-3">
                Consensus Rounds ({legacyEntries.length})
              </h4>
              <div className="space-y-3">
                {legacyEntries.map((entry, idx) => (
                  <ConsensusRound key={idx} entry={entry} index={idx} />
                ))}
              </div>
            </div>
          )}

          {/* Additional Consensus Data */}
          {consensusData && Object.keys(consensusData).length > 0 && (
            <div>
              <h4 className="font-medium text-gray-700 mb-2">Additional Consensus Data</h4>
              <div className="bg-gray-50 p-4 rounded">
                <JsonViewer data={consensusData} initialExpanded={true} />
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
