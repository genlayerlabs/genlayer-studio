import { format } from 'date-fns';
import { Card, CardContent } from '@/components/ui/card';
import { Clock } from 'lucide-react';

interface TimingInformationProps {
  lastVoteTimestamp: number | null | undefined;
  timestampAppeal: number | null | undefined;
  timestampAwaitingFinalization: number | null | undefined;
}

function formatTs(ts: number | null | undefined): string {
  if (ts === null || ts === undefined) return '-';
  const tsNum = Number(ts);
  const date = new Date(tsNum < 1e12 ? tsNum * 1000 : tsNum);
  return isNaN(date.getTime()) ? String(ts) : format(date, 'PPpp');
}

export function TimingInformation({
  lastVoteTimestamp,
  timestampAppeal,
  timestampAwaitingFinalization,
}: TimingInformationProps) {
  return (
    <div>
      <h3 className="text-lg font-semibold text-foreground mb-4 flex items-center gap-2">
        <Clock className="w-5 h-5 text-muted-foreground" />
        Timing Information
      </h3>
      <Card>
        <CardContent className="p-5">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {lastVoteTimestamp !== null && lastVoteTimestamp !== undefined && (
              <div>
                <p className="text-muted-foreground text-xs font-medium mb-1">Last Vote</p>
                <p className="text-foreground font-mono text-sm">{formatTs(lastVoteTimestamp)}</p>
              </div>
            )}
            {timestampAppeal !== null && timestampAppeal !== undefined && (
              <div>
                <p className="text-muted-foreground text-xs font-medium mb-1">Appeal Timestamp</p>
                <p className="text-foreground font-mono text-sm">{formatTs(timestampAppeal)}</p>
              </div>
            )}
            {timestampAwaitingFinalization !== null && timestampAwaitingFinalization !== undefined && (
              <div>
                <p className="text-muted-foreground text-xs font-medium mb-1">Awaiting Finalization</p>
                <p className="text-foreground font-mono text-sm">{formatTs(timestampAwaitingFinalization)}</p>
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
