'use client';

import { Transaction, ConsensusHistoryEntry } from '@/lib/types';
import { Card, CardContent } from '@/components/ui/card';
import { isNewConsensusFormat } from '@/lib/consensusUtils';
import { Activity } from 'lucide-react';
import { MonitoringStats } from '@/components/monitoring/MonitoringStats';
import { NewFormatTimeline } from '@/components/monitoring/NewFormatTimeline';
import { LegacyFormatTimeline } from '@/components/monitoring/LegacyFormatTimeline';
import { TimingInformation } from '@/components/monitoring/TimingInformation';
import { AppealProcessing } from '@/components/monitoring/AppealProcessing';

interface MonitoringTabProps {
  transaction: Transaction;
}

export function MonitoringTab({ transaction: tx }: MonitoringTabProps) {
  return (
    <div className="space-y-6">
      <MonitoringStats
        consensusHistory={tx.consensus_history}
        numOfInitialValidators={tx.num_of_initial_validators}
        rotationCount={tx.rotation_count}
        appealed={tx.appealed}
      />

      {tx.consensus_history && isNewConsensusFormat(tx.consensus_history) ? (
        <NewFormatTimeline consensusHistory={tx.consensus_history} />
      ) : tx.consensus_history && Array.isArray(tx.consensus_history) && tx.consensus_history.length > 0 ? (
        <LegacyFormatTimeline consensusHistory={tx.consensus_history as ConsensusHistoryEntry[]} />
      ) : (
        <Card className="bg-muted/50">
          <CardContent className="p-8 text-center">
            <Activity className="w-12 h-12 text-muted-foreground/30 mx-auto mb-3" />
            <p className="text-foreground font-medium">No consensus history available</p>
            <p className="text-muted-foreground text-sm mt-1">
              This transaction may not have completed consensus yet
            </p>
          </CardContent>
        </Card>
      )}

      {(tx.last_vote_timestamp || tx.timestamp_appeal || tx.timestamp_awaiting_finalization) && (
        <TimingInformation
          lastVoteTimestamp={tx.last_vote_timestamp}
          timestampAppeal={tx.timestamp_appeal}
          timestampAwaitingFinalization={tx.timestamp_awaiting_finalization}
        />
      )}

      {tx.appeal_processing_time !== null && tx.appeal_processing_time !== undefined && (
        <AppealProcessing
          appealProcessingTime={tx.appeal_processing_time}
          appealFailed={tx.appeal_failed}
        />
      )}
    </div>
  );
}
