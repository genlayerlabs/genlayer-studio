'use client';

import { useState } from 'react';
import { ChevronDown, ChevronRight, User, Users, CheckCircle, XCircle, Clock } from 'lucide-react';
import { JsonViewer } from './JsonViewer';
import { VoteIcon } from './VoteIcon';
import { truncateAddress } from '@/lib/formatters';

export interface LegacyConsensusEntry {
  leader?: {
    address?: string;
    validator_id?: number;
    vote?: string;
    result?: unknown;
    calldata?: unknown;
    mode?: string;
    eq_outputs?: {
      leader?: unknown;
    };
  };
  validators?: Array<{
    address?: string;
    validator_id?: number;
    vote?: string;
    result?: unknown;
  }>;
  votes?: Array<{
    validator_address?: string;
    vote?: 'agree' | 'disagree' | 'timeout' | string;
    result?: unknown;
  }>;
  final?: boolean;
  round?: number;
}

interface ConsensusRoundProps {
  entry: LegacyConsensusEntry;
  index: number;
}

export function ConsensusRound({ entry, index }: ConsensusRoundProps) {
  const [expanded, setExpanded] = useState(index === 0);

  const voteStats = entry.votes?.reduce(
    (acc, v) => {
      if (v.vote === 'agree') acc.agree++;
      else if (v.vote === 'disagree') acc.disagree++;
      else if (v.vote === 'timeout') acc.timeout++;
      return acc;
    },
    { agree: 0, disagree: 0, timeout: 0 }
  ) || { agree: 0, disagree: 0, timeout: 0 };

  return (
    <div className="border rounded-lg overflow-hidden bg-white shadow-sm">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full px-4 py-3 flex items-center justify-between bg-gray-50 hover:bg-gray-100 transition-colors"
      >
        <div className="flex items-center gap-3">
          {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          <span className="font-medium">Round {entry.round ?? index + 1}</span>
          {entry.final && (
            <span className="bg-green-100 text-green-800 text-xs px-2 py-0.5 rounded-full">Final</span>
          )}
        </div>
        <div className="flex items-center gap-4 text-sm">
          <span className="flex items-center gap-1 text-green-600">
            <CheckCircle className="w-4 h-4" /> {voteStats.agree}
          </span>
          <span className="flex items-center gap-1 text-red-600">
            <XCircle className="w-4 h-4" /> {voteStats.disagree}
          </span>
          <span className="flex items-center gap-1 text-yellow-600">
            <Clock className="w-4 h-4" /> {voteStats.timeout}
          </span>
        </div>
      </button>

      {expanded && (
        <div className="p-4 space-y-4">
          {/* Leader Section */}
          {entry.leader && (
            <div className="border-l-4 border-blue-500 pl-4">
              <div className="flex items-center gap-2 mb-2">
                <User className="w-4 h-4 text-blue-500" />
                <span className="font-medium text-blue-700">Leader</span>
                {entry.leader.address && (
                  <code className="text-xs bg-gray-100 px-2 py-0.5 rounded font-mono">
                    {truncateAddress(entry.leader.address, 10, 8)}
                  </code>
                )}
                {entry.leader.validator_id !== undefined && (
                  <span className="text-xs text-gray-500">ID: {entry.leader.validator_id}</span>
                )}
              </div>
              {entry.leader.mode && (
                <div className="text-sm text-gray-600 mb-2">
                  Mode: <span className="font-medium">{entry.leader.mode}</span>
                </div>
              )}
              {entry.leader.result !== undefined && (
                <div className="mt-2">
                  <div className="text-sm text-gray-600 mb-1">Result:</div>
                  <div className="bg-gray-50 p-2 rounded text-sm overflow-auto max-h-40">
                    <JsonViewer data={entry.leader.result} initialExpanded={false} />
                  </div>
                </div>
              )}
              {entry.leader.calldata !== undefined && (
                <div className="mt-2">
                  <div className="text-sm text-gray-600 mb-1">Calldata:</div>
                  <div className="bg-gray-50 p-2 rounded text-sm overflow-auto max-h-40">
                    <JsonViewer data={entry.leader.calldata} initialExpanded={false} />
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Validators Section */}
          {entry.validators && entry.validators.length > 0 && (
            <div className="border-l-4 border-purple-500 pl-4">
              <div className="flex items-center gap-2 mb-2">
                <Users className="w-4 h-4 text-purple-500" />
                <span className="font-medium text-purple-700">Validators ({entry.validators.length})</span>
              </div>
              <div className="space-y-2">
                {entry.validators.map((validator, vIdx) => (
                  <div key={vIdx} className="bg-gray-50 p-2 rounded text-sm">
                    <div className="flex items-center gap-2 mb-1">
                      {validator.address && (
                        <code className="text-xs bg-white px-2 py-0.5 rounded font-mono">
                          {truncateAddress(validator.address, 10, 8)}
                        </code>
                      )}
                      {validator.validator_id !== undefined && (
                        <span className="text-xs text-gray-500">ID: {validator.validator_id}</span>
                      )}
                    </div>
                    {validator.result !== undefined && (
                      <div className="mt-1">
                        <JsonViewer data={validator.result} initialExpanded={false} />
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Votes Section */}
          {entry.votes && entry.votes.length > 0 && (
            <div className="border-l-4 border-green-500 pl-4">
              <div className="flex items-center gap-2 mb-2">
                <CheckCircle className="w-4 h-4 text-green-500" />
                <span className="font-medium text-green-700">Votes ({entry.votes.length})</span>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {entry.votes.map((vote, vIdx) => (
                  <div
                    key={vIdx}
                    className={`p-2 rounded border flex items-center gap-2 ${
                      vote.vote === 'agree'
                        ? 'bg-green-50 border-green-200'
                        : vote.vote === 'disagree'
                        ? 'bg-red-50 border-red-200'
                        : 'bg-yellow-50 border-yellow-200'
                    }`}
                  >
                    <VoteIcon vote={vote.vote} />
                    <div className="flex-1 min-w-0">
                      {vote.validator_address && (
                        <code className="text-xs font-mono block truncate">
                          {vote.validator_address}
                        </code>
                      )}
                    </div>
                    <span className="text-xs font-medium capitalize">{vote.vote}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
