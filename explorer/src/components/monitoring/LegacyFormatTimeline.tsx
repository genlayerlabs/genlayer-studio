import { ConsensusHistoryEntry } from '@/lib/types';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import {
  Activity,
  CheckCircle,
  XCircle,
  Timer,
  Check,
  Crown,
} from 'lucide-react';

interface LegacyFormatTimelineProps {
  consensusHistory: ConsensusHistoryEntry[];
}

export function LegacyFormatTimeline({ consensusHistory }: LegacyFormatTimelineProps) {
  return (
    <div>
      <h3 className="text-lg font-semibold text-foreground mb-4 flex items-center gap-2">
        <Activity className="w-5 h-5 text-primary" />
        Consensus Timeline
      </h3>
      <div className="relative">
        <div className="absolute left-6 top-0 bottom-0 w-0.5 bg-border"></div>

        <div className="space-y-6">
          {consensusHistory.map((round, idx) => {
            const votes = round.votes || [];
            const agreeCount = votes.filter((v) => v.vote === 'agree').length;
            const disagreeCount = votes.filter((v) => v.vote === 'disagree').length;
            const timeoutCount = votes.filter((v) => v.vote === 'timeout').length;
            const totalVotes = votes.length;
            const leader = round.leader;
            const isFinal = round.final;

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
                        Round {(round.round as number) ?? idx + 1}
                      </span>
                      {isFinal && (
                        <Badge className="bg-emerald-50 dark:bg-emerald-950 text-emerald-700 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800">
                          Final
                        </Badge>
                      )}
                    </div>
                    {totalVotes > 0 && (
                      <div className="flex items-center gap-3 text-sm">
                        <span className="flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
                          <CheckCircle className="w-4 h-4" />
                          {agreeCount}
                        </span>
                        <span className="flex items-center gap-1 text-red-600 dark:text-red-400">
                          <XCircle className="w-4 h-4" />
                          {disagreeCount}
                        </span>
                        <span className="flex items-center gap-1 text-amber-600 dark:text-amber-400">
                          <Timer className="w-4 h-4" />
                          {timeoutCount}
                        </span>
                      </div>
                    )}
                  </div>

                  {leader && (
                    <div className="mb-4 p-3 bg-blue-50 dark:bg-blue-950 rounded-lg border border-blue-100 dark:border-blue-800">
                      <div className="flex items-center gap-2 mb-2">
                        <Crown className="w-4 h-4 text-blue-600 dark:text-blue-400" />
                        <span className="text-sm font-semibold text-blue-800 dark:text-blue-300">Leader</span>
                      </div>
                      <div className="space-y-1 text-sm">
                        {leader.address && (
                          <div className="flex items-center gap-2">
                            <span className="text-muted-foreground">Address:</span>
                            <code className="text-xs bg-card px-2 py-0.5 rounded font-mono text-primary">
                              {String(leader.address).slice(0, 10)}...{String(leader.address).slice(-8)}
                            </code>
                          </div>
                        )}
                        {leader.mode && (
                          <div className="flex items-center gap-2">
                            <span className="text-muted-foreground">Mode:</span>
                            <span className="text-foreground font-medium">{String(leader.mode)}</span>
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {totalVotes > 0 && (
                    <div className="mb-4">
                      <div className="flex items-center justify-between text-xs text-muted-foreground mb-1">
                        <span>Vote Distribution</span>
                        <span>{totalVotes} votes</span>
                      </div>
                      <div className="h-3 bg-muted rounded-full overflow-hidden flex">
                        {agreeCount > 0 && (
                          <div
                            className="bg-emerald-500 h-full transition-all duration-300"
                            style={{ width: `${(agreeCount / totalVotes) * 100}%` }}
                          />
                        )}
                        {disagreeCount > 0 && (
                          <div
                            className="bg-red-500 h-full transition-all duration-300"
                            style={{ width: `${(disagreeCount / totalVotes) * 100}%` }}
                          />
                        )}
                        {timeoutCount > 0 && (
                          <div
                            className="bg-amber-500 h-full transition-all duration-300"
                            style={{ width: `${(timeoutCount / totalVotes) * 100}%` }}
                          />
                        )}
                      </div>
                    </div>
                  )}

                  {votes.length > 0 && (
                    <div>
                      <div className="text-xs text-muted-foreground mb-2">Validator Votes</div>
                      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
                        {votes.map((vote, vIdx) => (
                          <div
                            key={vIdx}
                            className={`px-3 py-2 rounded-lg text-xs font-medium flex items-center gap-2 ${
                              vote.vote === 'agree'
                                ? 'bg-emerald-50 dark:bg-emerald-950 text-emerald-700 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-800'
                                : vote.vote === 'disagree'
                                ? 'bg-red-50 dark:bg-red-950 text-red-700 dark:text-red-400 border border-red-200 dark:border-red-800'
                                : 'bg-amber-50 dark:bg-amber-950 text-amber-700 dark:text-amber-400 border border-amber-200 dark:border-amber-800'
                            }`}
                          >
                            {vote.vote === 'agree' && <CheckCircle className="w-3 h-3" />}
                            {vote.vote === 'disagree' && <XCircle className="w-3 h-3" />}
                            {vote.vote === 'timeout' && <Timer className="w-3 h-3" />}
                            <span className="truncate">
                              {vote.validator_address
                                ? `${vote.validator_address.slice(0, 6)}...${vote.validator_address.slice(-4)}`
                                : `Validator ${vIdx + 1}`}
                            </span>
                          </div>
                        ))}
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
  );
}
