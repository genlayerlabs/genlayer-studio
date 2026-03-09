'use client';

import { ConsensusRoundMonitoring } from '@/lib/types';
import { formatTimestamp, getDuration } from '@/lib/formatters';
import { getPhaseColor } from '@/lib/consensusUtils';
import { Card } from '@/components/ui/card';

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
        <h4 className="text-sm font-semibold text-foreground">{title}</h4>
      )}

      <div className="flex gap-4 text-sm">
        <div className="bg-muted px-3 py-1.5 rounded-lg">
          <span className="text-muted-foreground">Total Duration: </span>
          <span className="font-semibold text-foreground">{getDuration(startTime, endTime)}</span>
        </div>
        <div className="bg-muted px-3 py-1.5 rounded-lg">
          <span className="text-muted-foreground">Events: </span>
          <span className="font-semibold text-foreground">{entries.length}</span>
        </div>
      </div>

      <div className="relative">
        <div className="absolute left-3 top-0 bottom-0 w-0.5 bg-border"></div>

        <div className="space-y-2">
          {entries.map(([key, timestamp], idx) => {
            const relativeTime = idx > 0 ? getDuration(entries[idx - 1][1], timestamp) : null;

            return (
              <div key={key} className="relative pl-8">
                <div className={`absolute left-1.5 w-3 h-3 rounded-full ${getPhaseColor(key)} ring-2 ring-background`}></div>

                <Card className="flex items-center justify-between px-3 py-2 hover:shadow-sm transition-shadow">
                  <div className="flex items-center gap-3">
                    <code className="text-xs font-mono text-foreground bg-muted px-2 py-0.5 rounded">
                      {key}
                    </code>
                    {relativeTime && (
                      <span className="text-xs text-muted-foreground">+{relativeTime}</span>
                    )}
                  </div>
                  <span className="text-xs text-muted-foreground font-mono">
                    {formatTimestamp(timestamp)}
                  </span>
                </Card>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
