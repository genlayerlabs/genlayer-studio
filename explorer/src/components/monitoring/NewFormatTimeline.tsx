import { NewConsensusHistory } from '@/lib/types';
import { JsonViewer } from '@/components/JsonViewer';
import { MonitoringTimeline } from '@/components/MonitoringTimeline';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Activity, Check } from 'lucide-react';

interface NewFormatTimelineProps {
  consensusHistory: NewConsensusHistory;
}

export function NewFormatTimeline({ consensusHistory }: NewFormatTimelineProps) {
  const firstRound = consensusHistory.consensus_results[0];
  const globalStartTime = firstRound?.monitoring?.PENDING;

  return (
    <div className="space-y-6">
      {consensusHistory.current_status_changes.length > 0 && (
        <div>
          <h3 className="text-lg font-semibold text-foreground mb-4 flex items-center gap-2">
            <Activity className="w-5 h-5 text-primary" />
            Current Status
          </h3>
          <div className="flex flex-wrap gap-2">
            {consensusHistory.current_status_changes.map((status, idx) => (
              <Badge
                key={idx}
                className="bg-emerald-100 dark:bg-emerald-900 text-emerald-800 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800"
              >
                {status}
              </Badge>
            ))}
          </div>
        </div>
      )}

      {Object.keys(consensusHistory.current_monitoring).length > 0 && (
        <Card className="bg-muted/50">
          <CardContent className="p-5">
            <MonitoringTimeline
              monitoring={consensusHistory.current_monitoring}
              title="Current Monitoring Events"
              globalStartTime={globalStartTime}
            />
          </CardContent>
        </Card>
      )}

      {consensusHistory.consensus_results.length > 0 && (
        <div>
          <h3 className="text-lg font-semibold text-foreground mb-4 flex items-center gap-2">
            <Activity className="w-5 h-5 text-primary" />
            Consensus Rounds ({consensusHistory.consensus_results.length})
          </h3>
          <div className="relative">
            <div className="absolute left-6 top-0 bottom-0 w-0.5 bg-border"></div>

            <div className="space-y-6">
              {consensusHistory.consensus_results.map((result, idx) => {
                const isFinal = result.consensus_round === 'Accepted' || result.consensus_round === 'Finalized';

                return (
                  <div key={idx} className="relative pl-16">
                    <div className={`absolute left-4 w-5 h-5 rounded-full border-2 ${
                      isFinal
                        ? 'bg-emerald-500 border-emerald-500'
                        : 'bg-card border-primary'
                    } flex items-center justify-center`}>
                      {isFinal && <Check className="w-3 h-3 text-white" />}
                    </div>

                    <Card className="p-5 hover:shadow-md transition-shadow">
                      <div className="flex items-center justify-between mb-4">
                        <div className="flex items-center gap-3">
                          <span className="text-lg font-semibold text-foreground">
                            Round {idx + 1}
                          </span>
                          <Badge className={
                            isFinal
                              ? 'bg-emerald-50 dark:bg-emerald-950 text-emerald-700 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800'
                              : 'bg-blue-50 dark:bg-blue-950 text-blue-700 dark:text-blue-400 border-blue-200 dark:border-blue-800'
                          }>
                            {result.consensus_round}
                          </Badge>
                        </div>
                      </div>

                      {result.status_changes.length > 0 && (
                        <div className="mb-4">
                          <div className="text-xs text-muted-foreground mb-2">Status Flow</div>
                          <div className="flex flex-wrap items-center gap-2">
                            {result.status_changes.map((status, sIdx) => (
                              <div key={sIdx} className="flex items-center gap-2">
                                <Badge className={
                                  status === 'ACCEPTED' || status === 'FINALIZED'
                                    ? 'bg-emerald-100 dark:bg-emerald-900 text-emerald-700 dark:text-emerald-400'
                                    : status === 'PENDING'
                                    ? 'bg-muted text-muted-foreground'
                                    : 'bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-400'
                                }>
                                  {status}
                                </Badge>
                                {sIdx < result.status_changes.length - 1 && (
                                  <span className="text-muted-foreground/50">→</span>
                                )}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {Object.keys(result.monitoring).length > 0 && (
                        <div className="border-t border-border pt-4">
                          <MonitoringTimeline monitoring={result.monitoring} />
                        </div>
                      )}

                      {result.validator_results.length > 0 && (
                        <div className="border-t border-border pt-4 mt-4">
                          <div className="text-xs text-muted-foreground mb-2">Validator Results</div>
                          <div className="bg-muted rounded-lg p-3">
                            <JsonViewer data={result.validator_results} />
                          </div>
                        </div>
                      )}
                    </Card>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
