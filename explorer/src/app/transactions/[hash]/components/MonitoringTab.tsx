'use client';

import { format } from 'date-fns';
import { Transaction, ConsensusHistoryEntry } from '@/lib/types';
import { JsonViewer } from '@/components/JsonViewer';
import { MonitoringTimeline } from '@/components/MonitoringTimeline';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { isNewConsensusFormat, getConsensusRoundCount } from '@/lib/consensusUtils';
import {
  Activity,
  Users,
  Timer,
  AlertTriangle,
  CheckCircle,
  XCircle,
  Clock,
  Check,
  Crown,
} from 'lucide-react';

interface MonitoringTabProps {
  transaction: Transaction;
}

export function MonitoringTab({ transaction: tx }: MonitoringTabProps) {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card className="bg-muted/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="bg-blue-100 dark:bg-blue-950 p-2 rounded-lg">
                <Activity className="w-5 h-5 text-blue-600 dark:text-blue-400" />
              </div>
              <div>
                <p className="text-muted-foreground text-xs font-medium">Consensus Rounds</p>
                <p className="text-xl font-bold text-foreground">
                  {getConsensusRoundCount(tx.consensus_history)}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="bg-muted/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="bg-emerald-100 dark:bg-emerald-950 p-2 rounded-lg">
                <Users className="w-5 h-5 text-emerald-600 dark:text-emerald-400" />
              </div>
              <div>
                <p className="text-muted-foreground text-xs font-medium">Validators</p>
                <p className="text-xl font-bold text-foreground">
                  {tx.num_of_initial_validators || '-'}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="bg-muted/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="bg-violet-100 dark:bg-violet-950 p-2 rounded-lg">
                <Timer className="w-5 h-5 text-violet-600 dark:text-violet-400" />
              </div>
              <div>
                <p className="text-muted-foreground text-xs font-medium">Rotations</p>
                <p className="text-xl font-bold text-foreground">
                  {tx.rotation_count ?? 0}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="bg-muted/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className={`p-2 rounded-lg ${tx.appealed ? 'bg-amber-100 dark:bg-amber-950' : 'bg-emerald-100 dark:bg-emerald-950'}`}>
                {tx.appealed ? (
                  <AlertTriangle className="w-5 h-5 text-amber-600 dark:text-amber-400" />
                ) : (
                  <CheckCircle className="w-5 h-5 text-emerald-600 dark:text-emerald-400" />
                )}
              </div>
              <div>
                <p className="text-muted-foreground text-xs font-medium">Appeal Status</p>
                <p className="text-xl font-bold text-foreground">
                  {tx.appealed ? 'Appealed' : 'None'}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {tx.consensus_history && isNewConsensusFormat(tx.consensus_history) ? (
        <NewFormatTimeline tx={tx} />
      ) : tx.consensus_history && Array.isArray(tx.consensus_history) && tx.consensus_history.length > 0 ? (
        <LegacyFormatTimeline tx={tx} />
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
        <TimingInformation tx={tx} />
      )}

      {tx.appeal_processing_time !== null && tx.appeal_processing_time !== undefined && (
        <AppealProcessing tx={tx} />
      )}
    </div>
  );
}

function NewFormatTimeline({ tx }: { tx: Transaction }) {
  if (!tx.consensus_history || !isNewConsensusFormat(tx.consensus_history)) return null;

  const consensusHistory = tx.consensus_history;
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

function LegacyFormatTimeline({ tx }: { tx: Transaction }) {
  if (!tx.consensus_history || !Array.isArray(tx.consensus_history)) return null;

  const consensusHistory = tx.consensus_history as ConsensusHistoryEntry[];

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

function TimingInformation({ tx }: { tx: Transaction }) {
  const formatTs = (ts: number | null | undefined): string => {
    if (ts === null || ts === undefined) return '-';
    const tsNum = Number(ts);
    const date = new Date(tsNum < 1e12 ? tsNum * 1000 : tsNum);
    return isNaN(date.getTime()) ? String(ts) : format(date, 'PPpp');
  };

  return (
    <div>
      <h3 className="text-lg font-semibold text-foreground mb-4 flex items-center gap-2">
        <Clock className="w-5 h-5 text-muted-foreground" />
        Timing Information
      </h3>
      <Card>
        <CardContent className="p-5">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {tx.last_vote_timestamp !== null && tx.last_vote_timestamp !== undefined && (
              <div>
                <p className="text-muted-foreground text-xs font-medium mb-1">Last Vote</p>
                <p className="text-foreground font-mono text-sm">{formatTs(tx.last_vote_timestamp)}</p>
              </div>
            )}
            {tx.timestamp_appeal !== null && tx.timestamp_appeal !== undefined && (
              <div>
                <p className="text-muted-foreground text-xs font-medium mb-1">Appeal Timestamp</p>
                <p className="text-foreground font-mono text-sm">{formatTs(tx.timestamp_appeal)}</p>
              </div>
            )}
            {tx.timestamp_awaiting_finalization !== null && tx.timestamp_awaiting_finalization !== undefined && (
              <div>
                <p className="text-muted-foreground text-xs font-medium mb-1">Awaiting Finalization</p>
                <p className="text-foreground font-mono text-sm">{formatTs(tx.timestamp_awaiting_finalization)}</p>
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function AppealProcessing({ tx }: { tx: Transaction }) {
  return (
    <div>
      <h3 className="text-lg font-semibold text-foreground mb-4 flex items-center gap-2">
        <Timer className="w-5 h-5 text-amber-600 dark:text-amber-400" />
        Appeal Processing
      </h3>
      <Card className="border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950">
        <CardContent className="p-5">
          <div className="flex items-center gap-4">
            <div>
              <p className="text-amber-700 dark:text-amber-400 text-xs font-medium mb-1">Processing Time</p>
              <p className="text-amber-900 dark:text-amber-200 text-2xl font-bold">{tx.appeal_processing_time}ms</p>
            </div>
            {tx.appeal_failed !== null && tx.appeal_failed !== undefined && (
              <div>
                <p className="text-amber-700 dark:text-amber-400 text-xs font-medium mb-1">Failed Appeals</p>
                <p className="text-amber-900 dark:text-amber-200 text-2xl font-bold">{tx.appeal_failed}</p>
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
