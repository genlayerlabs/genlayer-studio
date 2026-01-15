'use client';

import { format } from 'date-fns';
import { Transaction, ConsensusHistoryEntry } from '@/lib/types';
import { JsonViewer } from '@/components/JsonViewer';
import { MonitoringTimeline } from '@/components/MonitoringTimeline';
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
      {/* Monitoring Overview Stats */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="bg-slate-50 rounded-xl p-4 border border-slate-200">
          <div className="flex items-center gap-3">
            <div className="bg-blue-100 p-2 rounded-lg">
              <Activity className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <p className="text-slate-500 text-xs font-medium">Consensus Rounds</p>
              <p className="text-xl font-bold text-slate-900">
                {getConsensusRoundCount(tx.consensus_history)}
              </p>
            </div>
          </div>
        </div>
        <div className="bg-slate-50 rounded-xl p-4 border border-slate-200">
          <div className="flex items-center gap-3">
            <div className="bg-emerald-100 p-2 rounded-lg">
              <Users className="w-5 h-5 text-emerald-600" />
            </div>
            <div>
              <p className="text-slate-500 text-xs font-medium">Validators</p>
              <p className="text-xl font-bold text-slate-900">
                {tx.num_of_initial_validators || '-'}
              </p>
            </div>
          </div>
        </div>
        <div className="bg-slate-50 rounded-xl p-4 border border-slate-200">
          <div className="flex items-center gap-3">
            <div className="bg-violet-100 p-2 rounded-lg">
              <Timer className="w-5 h-5 text-violet-600" />
            </div>
            <div>
              <p className="text-slate-500 text-xs font-medium">Rotations</p>
              <p className="text-xl font-bold text-slate-900">
                {tx.rotation_count ?? 0}
              </p>
            </div>
          </div>
        </div>
        <div className="bg-slate-50 rounded-xl p-4 border border-slate-200">
          <div className="flex items-center gap-3">
            <div className={`p-2 rounded-lg ${tx.appealed ? 'bg-amber-100' : 'bg-emerald-100'}`}>
              {tx.appealed ? (
                <AlertTriangle className="w-5 h-5 text-amber-600" />
              ) : (
                <CheckCircle className="w-5 h-5 text-emerald-600" />
              )}
            </div>
            <div>
              <p className="text-slate-500 text-xs font-medium">Appeal Status</p>
              <p className="text-xl font-bold text-slate-900">
                {tx.appealed ? 'Appealed' : 'None'}
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Consensus Timeline - handles both legacy and new format */}
      {tx.consensus_history && isNewConsensusFormat(tx.consensus_history) ? (
        <NewFormatTimeline tx={tx} />
      ) : tx.consensus_history && Array.isArray(tx.consensus_history) && tx.consensus_history.length > 0 ? (
        <LegacyFormatTimeline tx={tx} />
      ) : (
        <div className="bg-slate-50 rounded-xl p-8 text-center border border-slate-200">
          <Activity className="w-12 h-12 text-slate-300 mx-auto mb-3" />
          <p className="text-slate-600 font-medium">No consensus history available</p>
          <p className="text-slate-400 text-sm mt-1">
            This transaction may not have completed consensus yet
          </p>
        </div>
      )}

      {/* Timing Information */}
      {(tx.last_vote_timestamp || tx.timestamp_appeal || tx.timestamp_awaiting_finalization) && (
        <TimingInformation tx={tx} />
      )}

      {/* Appeal Processing */}
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
      {/* Current Status */}
      {consensusHistory.current_status_changes.length > 0 && (
        <div>
          <h3 className="text-lg font-semibold text-slate-900 mb-4 flex items-center gap-2">
            <Activity className="w-5 h-5 text-blue-600" />
            Current Status
          </h3>
          <div className="flex flex-wrap gap-2">
            {consensusHistory.current_status_changes.map((status, idx) => (
              <span
                key={idx}
                className="bg-emerald-100 text-emerald-800 px-3 py-1.5 rounded-full text-sm font-medium"
              >
                {status}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Current Monitoring Timeline */}
      {Object.keys(consensusHistory.current_monitoring).length > 0 && (
        <div className="bg-slate-50 rounded-xl p-5 border border-slate-200">
          <MonitoringTimeline
            monitoring={consensusHistory.current_monitoring}
            title="Current Monitoring Events"
            globalStartTime={globalStartTime}
          />
        </div>
      )}

      {/* Consensus Results */}
      {consensusHistory.consensus_results.length > 0 && (
        <div>
          <h3 className="text-lg font-semibold text-slate-900 mb-4 flex items-center gap-2">
            <Activity className="w-5 h-5 text-blue-600" />
            Consensus Rounds ({consensusHistory.consensus_results.length})
          </h3>
          <div className="relative">
            <div className="absolute left-6 top-0 bottom-0 w-0.5 bg-slate-200"></div>

            <div className="space-y-6">
              {consensusHistory.consensus_results.map((result, idx) => {
                const isFinal = result.consensus_round === 'Accepted' || result.consensus_round === 'Finalized';

                return (
                  <div key={idx} className="relative pl-16">
                    <div className={`absolute left-4 w-5 h-5 rounded-full border-2 ${
                      isFinal
                        ? 'bg-emerald-500 border-emerald-500'
                        : 'bg-white border-blue-500'
                    } flex items-center justify-center`}>
                      {isFinal && <Check className="w-3 h-3 text-white" />}
                    </div>

                    <div className="bg-white rounded-xl border border-slate-200 p-5 hover:shadow-md transition-shadow">
                      {/* Round Header */}
                      <div className="flex items-center justify-between mb-4">
                        <div className="flex items-center gap-3">
                          <span className="text-lg font-semibold text-slate-900">
                            Round {idx + 1}
                          </span>
                          <span className={`text-xs font-semibold px-2.5 py-1 rounded-lg ${
                            isFinal
                              ? 'bg-emerald-50 text-emerald-700'
                              : 'bg-blue-50 text-blue-700'
                          }`}>
                            {result.consensus_round}
                          </span>
                        </div>
                      </div>

                      {/* Status Changes Flow */}
                      {result.status_changes.length > 0 && (
                        <div className="mb-4">
                          <div className="text-xs text-slate-500 mb-2">Status Flow</div>
                          <div className="flex flex-wrap items-center gap-2">
                            {result.status_changes.map((status, sIdx) => (
                              <div key={sIdx} className="flex items-center gap-2">
                                <span className={`px-2.5 py-1 rounded-lg text-xs font-medium ${
                                  status === 'ACCEPTED' || status === 'FINALIZED'
                                    ? 'bg-emerald-100 text-emerald-700'
                                    : status === 'PENDING'
                                    ? 'bg-slate-100 text-slate-700'
                                    : 'bg-blue-100 text-blue-700'
                                }`}>
                                  {status}
                                </span>
                                {sIdx < result.status_changes.length - 1 && (
                                  <span className="text-slate-300">→</span>
                                )}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Monitoring Timeline */}
                      {Object.keys(result.monitoring).length > 0 && (
                        <div className="border-t border-slate-100 pt-4">
                          <MonitoringTimeline monitoring={result.monitoring} />
                        </div>
                      )}

                      {/* Validator Results */}
                      {result.validator_results.length > 0 && (
                        <div className="border-t border-slate-100 pt-4 mt-4">
                          <div className="text-xs text-slate-500 mb-2">Validator Results</div>
                          <div className="bg-slate-50 rounded-lg p-3">
                            <JsonViewer data={result.validator_results} />
                          </div>
                        </div>
                      )}
                    </div>
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
      <h3 className="text-lg font-semibold text-slate-900 mb-4 flex items-center gap-2">
        <Activity className="w-5 h-5 text-blue-600" />
        Consensus Timeline
      </h3>
      <div className="relative">
        <div className="absolute left-6 top-0 bottom-0 w-0.5 bg-slate-200"></div>

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
                    : 'bg-white border-blue-500'
                } flex items-center justify-center`}>
                  {isFinal && <Check className="w-3 h-3 text-white" />}
                </div>

                <div className="bg-white rounded-xl border border-slate-200 p-5 hover:shadow-md transition-shadow">
                  {/* Round Header */}
                  <div className="flex items-center justify-between mb-4">
                    <div className="flex items-center gap-3">
                      <span className="text-lg font-semibold text-slate-900">
                        Round {(round.round as number) ?? idx + 1}
                      </span>
                      {isFinal && (
                        <span className="bg-emerald-50 text-emerald-700 text-xs font-semibold px-2.5 py-1 rounded-lg">
                          Final
                        </span>
                      )}
                    </div>
                    {totalVotes > 0 && (
                      <div className="flex items-center gap-3 text-sm">
                        <span className="flex items-center gap-1 text-emerald-600">
                          <CheckCircle className="w-4 h-4" />
                          {agreeCount}
                        </span>
                        <span className="flex items-center gap-1 text-red-600">
                          <XCircle className="w-4 h-4" />
                          {disagreeCount}
                        </span>
                        <span className="flex items-center gap-1 text-amber-600">
                          <Timer className="w-4 h-4" />
                          {timeoutCount}
                        </span>
                      </div>
                    )}
                  </div>

                  {/* Leader Info */}
                  {leader && (
                    <div className="mb-4 p-3 bg-blue-50 rounded-lg border border-blue-100">
                      <div className="flex items-center gap-2 mb-2">
                        <Crown className="w-4 h-4 text-blue-600" />
                        <span className="text-sm font-semibold text-blue-800">Leader</span>
                      </div>
                      <div className="space-y-1 text-sm">
                        {leader.address && (
                          <div className="flex items-center gap-2">
                            <span className="text-slate-500">Address:</span>
                            <code className="text-xs bg-white px-2 py-0.5 rounded font-mono text-blue-700">
                              {String(leader.address).slice(0, 10)}...{String(leader.address).slice(-8)}
                            </code>
                          </div>
                        )}
                        {leader.mode && (
                          <div className="flex items-center gap-2">
                            <span className="text-slate-500">Mode:</span>
                            <span className="text-slate-800 font-medium">{String(leader.mode)}</span>
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {/* Vote Distribution Bar */}
                  {totalVotes > 0 && (
                    <div className="mb-4">
                      <div className="flex items-center justify-between text-xs text-slate-500 mb-1">
                        <span>Vote Distribution</span>
                        <span>{totalVotes} votes</span>
                      </div>
                      <div className="h-3 bg-slate-100 rounded-full overflow-hidden flex">
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

                  {/* Individual Votes */}
                  {votes.length > 0 && (
                    <div>
                      <div className="text-xs text-slate-500 mb-2">Validator Votes</div>
                      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
                        {votes.map((vote, vIdx) => (
                          <div
                            key={vIdx}
                            className={`px-3 py-2 rounded-lg text-xs font-medium flex items-center gap-2 ${
                              vote.vote === 'agree'
                                ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                                : vote.vote === 'disagree'
                                ? 'bg-red-50 text-red-700 border border-red-200'
                                : 'bg-amber-50 text-amber-700 border border-amber-200'
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
                </div>
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
      <h3 className="text-lg font-semibold text-slate-900 mb-4 flex items-center gap-2">
        <Clock className="w-5 h-5 text-slate-600" />
        Timing Information
      </h3>
      <div className="bg-white rounded-xl border border-slate-200 p-5">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {tx.last_vote_timestamp !== null && tx.last_vote_timestamp !== undefined && (
            <div>
              <p className="text-slate-500 text-xs font-medium mb-1">Last Vote</p>
              <p className="text-slate-800 font-mono text-sm">{formatTs(tx.last_vote_timestamp)}</p>
            </div>
          )}
          {tx.timestamp_appeal !== null && tx.timestamp_appeal !== undefined && (
            <div>
              <p className="text-slate-500 text-xs font-medium mb-1">Appeal Timestamp</p>
              <p className="text-slate-800 font-mono text-sm">{formatTs(tx.timestamp_appeal)}</p>
            </div>
          )}
          {tx.timestamp_awaiting_finalization !== null && tx.timestamp_awaiting_finalization !== undefined && (
            <div>
              <p className="text-slate-500 text-xs font-medium mb-1">Awaiting Finalization</p>
              <p className="text-slate-800 font-mono text-sm">{formatTs(tx.timestamp_awaiting_finalization)}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function AppealProcessing({ tx }: { tx: Transaction }) {
  return (
    <div>
      <h3 className="text-lg font-semibold text-slate-900 mb-4 flex items-center gap-2">
        <Timer className="w-5 h-5 text-amber-600" />
        Appeal Processing
      </h3>
      <div className="bg-amber-50 rounded-xl border border-amber-200 p-5">
        <div className="flex items-center gap-4">
          <div>
            <p className="text-amber-700 text-xs font-medium mb-1">Processing Time</p>
            <p className="text-amber-900 text-2xl font-bold">{tx.appeal_processing_time}ms</p>
          </div>
          {tx.appeal_failed !== null && tx.appeal_failed !== undefined && (
            <div>
              <p className="text-amber-700 text-xs font-medium mb-1">Failed Appeals</p>
              <p className="text-amber-900 text-2xl font-bold">{tx.appeal_failed}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
