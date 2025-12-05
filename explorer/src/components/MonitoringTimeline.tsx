'use client';

import { ConsensusRoundMonitoring } from '@/lib/types';
import { formatTimestamp, getDuration } from '@/lib/formatters';
import { getPhaseColor } from '@/lib/consensusUtils';

interface MonitoringTimelineProps {
  monitoring: ConsensusRoundMonitoring;
  title?: string;
  globalStartTime?: number;
}

export function MonitoringTimeline({ monitoring, title, globalStartTime }: MonitoringTimelineProps) {
  const entries = Object.entries(monitoring).sort((a, b) => a[1] - b[1]);

  if (entries.length === 0) return null;

  const startTime = globalStartTime ?? entries[0][1];
  const endTime = entries[entries.length - 1][1];

  return (
    <div className="space-y-4">
      {title && (
        <h4 className="text-sm font-semibold text-slate-700">{title}</h4>
      )}

      {/* Summary stats */}
      <div className="flex gap-4 text-sm">
        <div className="bg-slate-100 px-3 py-1.5 rounded-lg">
          <span className="text-slate-500">Total Duration: </span>
          <span className="font-semibold text-slate-800">{getDuration(startTime, endTime)}</span>
        </div>
        <div className="bg-slate-100 px-3 py-1.5 rounded-lg">
          <span className="text-slate-500">Events: </span>
          <span className="font-semibold text-slate-800">{entries.length}</span>
        </div>
      </div>

      {/* Timeline */}
      <div className="relative">
        <div className="absolute left-3 top-0 bottom-0 w-0.5 bg-slate-200"></div>

        <div className="space-y-2">
          {entries.map(([key, timestamp], idx) => {
            const relativeTime = idx > 0 ? getDuration(entries[idx - 1][1], timestamp) : null;

            return (
              <div key={key} className="relative pl-8">
                <div className={`absolute left-1.5 w-3 h-3 rounded-full ${getPhaseColor(key)} ring-2 ring-white`}></div>

                <div className="flex items-center justify-between bg-white border border-slate-200 rounded-lg px-3 py-2 hover:shadow-sm transition-shadow">
                  <div className="flex items-center gap-3">
                    <code className="text-xs font-mono text-slate-700 bg-slate-100 px-2 py-0.5 rounded">
                      {key}
                    </code>
                    {relativeTime && (
                      <span className="text-xs text-slate-400">+{relativeTime}</span>
                    )}
                  </div>
                  <span className="text-xs text-slate-500 font-mono">
                    {formatTimestamp(timestamp)}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
