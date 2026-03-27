import { Card, CardContent } from '@/components/ui/card';
import { Timer } from 'lucide-react';

interface AppealProcessingProps {
  appealProcessingTime: number;
  appealFailed: number | null | undefined;
}

export function AppealProcessing({
  appealProcessingTime,
  appealFailed,
}: AppealProcessingProps) {
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
              <p className="text-amber-900 dark:text-amber-200 text-2xl font-bold">{appealProcessingTime}ms</p>
            </div>
            {appealFailed !== null && appealFailed !== undefined && (
              <div>
                <p className="text-amber-700 dark:text-amber-400 text-xs font-medium mb-1">Failed Appeals</p>
                <p className="text-amber-900 dark:text-amber-200 text-2xl font-bold">{appealFailed}</p>
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
