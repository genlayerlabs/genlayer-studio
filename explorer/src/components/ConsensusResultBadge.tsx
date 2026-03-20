'use client';

import { cn } from '@/lib/utils';

const FAILURE_RESULTS = new Set(['Undetermined', 'Leader Timeout', 'Validators Timeout']);

interface ConsensusResultBadgeProps {
  result: string;
  /** Use 'badge' for bordered Badge style (detail views), 'inline' for table cells */
  variant?: 'inline' | 'badge';
}

function getResultColorClasses(result: string, variant: 'inline' | 'badge') {
  if (result === 'Accepted') {
    return variant === 'badge'
      ? 'bg-green-50 dark:bg-green-950 text-green-700 dark:text-green-400 border-green-200 dark:border-green-800'
      : 'bg-green-50 dark:bg-green-950 text-green-700 dark:text-green-400';
  }
  if (FAILURE_RESULTS.has(result)) {
    return variant === 'badge'
      ? 'bg-red-50 dark:bg-red-950 text-red-700 dark:text-red-400 border-red-200 dark:border-red-800'
      : 'bg-red-50 dark:bg-red-950 text-red-700 dark:text-red-400';
  }
  return variant === 'badge'
    ? 'bg-amber-50 dark:bg-amber-950 text-amber-700 dark:text-amber-400 border-amber-200 dark:border-amber-800'
    : 'bg-amber-50 dark:bg-amber-950 text-amber-700 dark:text-amber-400';
}

export function ConsensusResultBadge({ result, variant = 'inline' }: ConsensusResultBadgeProps) {
  return (
    <span className={cn(
      'px-2 py-1 rounded-lg text-xs font-semibold',
      variant === 'badge' && 'border',
      getResultColorClasses(result, variant),
    )}>
      {result}
    </span>
  );
}
