import { Card, CardContent } from '@/components/ui/card';
import { Activity, Users, Timer, AlertTriangle, CheckCircle } from 'lucide-react';
import { ConsensusHistoryData } from '@/lib/types';
import { getConsensusRoundCount } from '@/lib/consensusUtils';

interface MonitoringStatsProps {
  consensusHistory: ConsensusHistoryData | null;
  numOfInitialValidators: number | null;
  rotationCount: number | null;
  appealed: boolean;
}

export function MonitoringStats({
  consensusHistory,
  numOfInitialValidators,
  rotationCount,
  appealed,
}: MonitoringStatsProps) {
  return (
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
                {getConsensusRoundCount(consensusHistory)}
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
                {numOfInitialValidators || '-'}
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
                {rotationCount ?? 0}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
      <Card className="bg-muted/50">
        <CardContent className="p-4">
          <div className="flex items-center gap-3">
            <div className={`p-2 rounded-lg ${appealed ? 'bg-amber-100 dark:bg-amber-950' : 'bg-emerald-100 dark:bg-emerald-950'}`}>
              {appealed ? (
                <AlertTriangle className="w-5 h-5 text-amber-600 dark:text-amber-400" />
              ) : (
                <CheckCircle className="w-5 h-5 text-emerald-600 dark:text-emerald-400" />
              )}
            </div>
            <div>
              <p className="text-muted-foreground text-xs font-medium">Appeal Status</p>
              <p className="text-xl font-bold text-foreground">
                {appealed ? 'Appealed' : 'None'}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
