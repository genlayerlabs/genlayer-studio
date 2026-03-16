'use client';

import { useMemo } from 'react';
import { Check, AlertTriangle } from 'lucide-react';
import { CONSENSUS_PHASES } from '@/lib/consensusUtils';
import { isNewConsensusFormat } from '@/lib/consensusUtils';
import { formatTimestamp } from '@/lib/formatters';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import type { Transaction, TransactionStatus } from '@/lib/types';

const JOURNEY_STEPS = [
  'PENDING',
  'PROPOSING',
  'COMMITTING',
  'REVEALING',
  'ACCEPTED',
  'FINALIZED',
] as const;

type JourneyStep = (typeof JOURNEY_STEPS)[number];

const FAILED_STATUSES: TransactionStatus[] = [
  'UNDETERMINED',
  'LEADER_TIMEOUT',
  'VALIDATORS_TIMEOUT',
  'CANCELED',
];

// Map tx.status to the farthest step reached
const STATUS_TO_STEP_INDEX: Record<string, number> = {
  PENDING: 0,
  ACTIVATED: 0,
  PROPOSING: 1,
  COMMITTING: 2,
  REVEALING: 3,
  ACCEPTED: 4,
  FINALIZED: 5,
  // Failed states map to the step they failed at
  UNDETERMINED: 3,
  LEADER_TIMEOUT: 1,
  VALIDATORS_TIMEOUT: 2,
  CANCELED: 0,
};

interface ConsensusJourneyProps {
  transaction: Transaction;
}

export function ConsensusJourney({ transaction: tx }: ConsensusJourneyProps) {
  const { reachedIndex, timestamps, isFailed } = useMemo(() => {
    let reachedIdx = STATUS_TO_STEP_INDEX[tx.status] ?? 0;
    const ts: Partial<Record<JourneyStep, number>> = {};
    const failed = FAILED_STATUSES.includes(tx.status);

    // Try to extract timestamps from new consensus format
    if (tx.consensus_history && isNewConsensusFormat(tx.consensus_history)) {
      const history = tx.consensus_history;
      // Check current_monitoring first, then fall back to most recent round
      let monitoring = Object.keys(history.current_monitoring).length > 0
        ? history.current_monitoring
        : undefined;
      if (!monitoring && history.consensus_results.length > 0) {
        // Use the last round with a non-empty monitoring object
        for (let i = history.consensus_results.length - 1; i >= 0; i--) {
          const m = history.consensus_results[i]?.monitoring;
          if (m && Object.keys(m).length > 0) {
            monitoring = m;
            break;
          }
        }
      }

      if (monitoring) {
        for (const step of JOURNEY_STEPS) {
          if (monitoring[step] !== undefined) {
            ts[step] = monitoring[step];
          }
        }
        // Update reached index from monitoring timestamps
        for (let i = JOURNEY_STEPS.length - 1; i >= 0; i--) {
          if (ts[JOURNEY_STEPS[i]!] !== undefined) {
            reachedIdx = Math.max(reachedIdx, i);
            break;
          }
        }
      }
    }

    return { reachedIndex: reachedIdx, timestamps: ts, isFailed: failed };
  }, [tx.status, tx.consensus_history]);

  return (
    <div className="space-y-4">
      {/* Step bar */}
      <div className="flex items-center">
        {JOURNEY_STEPS.map((step, idx) => {
          const phase = CONSENSUS_PHASES[step];
          const isReached = idx <= reachedIndex;
          const isCurrent = idx === reachedIndex && !isFailed;
          const isFailedStep = idx === reachedIndex && isFailed;
          const timestamp = timestamps[step];

          const circleClassName = `relative w-8 h-8 rounded-full flex items-center justify-center shrink-0 transition-colors ${
                isFailedStep
                  ? 'bg-red-500 text-white'
                  : isReached
                  ? `${phase.color} text-white`
                  : 'bg-muted text-muted-foreground border-2 border-border'
              } ${isCurrent ? 'ring-2 ring-primary ring-offset-2 ring-offset-background' : ''}`;

          const circleContent = isFailedStep ? (
                <AlertTriangle className="w-4 h-4" />
              ) : isReached && idx < reachedIndex ? (
                <Check className="w-4 h-4" />
              ) : isCurrent ? (
                <span className="w-2 h-2 rounded-full bg-white animate-pulse" />
              ) : (
                <span className="text-[10px] font-bold">{idx + 1}</span>
              );

          const circle = timestamp ? (
            <button
              type="button"
              aria-label={`${phase.label} — ${formatTimestamp(timestamp)}`}
              className={`${circleClassName} cursor-default`}
            >
              {circleContent}
            </button>
          ) : (
            <span
              role="img"
              aria-label={`${phase.label}${isReached ? ' — reached' : ''}`}
              className={circleClassName}
            >
              {circleContent}
            </span>
          );

          return (
            <div key={step} className="flex items-center flex-1 last:flex-none">
              <div className="flex flex-col items-center gap-1">
                {timestamp ? (
                  <Tooltip>
                    <TooltipTrigger asChild>{circle}</TooltipTrigger>
                    <TooltipContent>
                      <p className="font-semibold">{phase.label}</p>
                      <p className="text-xs text-muted-foreground">{formatTimestamp(timestamp)}</p>
                    </TooltipContent>
                  </Tooltip>
                ) : (
                  circle
                )}
                <span
                  className={`text-[10px] font-medium ${
                    isReached ? 'text-foreground' : 'text-muted-foreground'
                  }`}
                >
                  {phase.label}
                </span>
              </div>

              {/* Connector line */}
              {idx < JOURNEY_STEPS.length - 1 && (
                <div
                  className={`flex-1 h-0.5 mx-1 mt-[-1rem] ${
                    idx < reachedIndex ? 'bg-primary' : 'bg-border'
                  }`}
                />
              )}
            </div>
          );
        })}
      </div>

      {/* Failed status banner */}
      {isFailed && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-400 text-sm">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          <span>
            Transaction ended with status: <strong>{tx.status.replace(/_/g, ' ')}</strong>
          </span>
        </div>
      )}
    </div>
  );
}
