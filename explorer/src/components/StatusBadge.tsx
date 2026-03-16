'use client';

import { TransactionStatus } from '@/lib/types';
import { cn } from '@/lib/utils';

const statusConfig: Record<TransactionStatus, { bg: string; text: string; dot: string }> = {
  PENDING: { bg: 'bg-amber-50 dark:bg-amber-950', text: 'text-amber-700 dark:text-amber-400', dot: 'bg-amber-500' },
  ACTIVATED: { bg: 'bg-blue-50 dark:bg-blue-950', text: 'text-blue-700 dark:text-blue-400', dot: 'bg-blue-500' },
  CANCELED: { bg: 'bg-muted', text: 'text-muted-foreground', dot: 'bg-muted-foreground' },
  PROPOSING: { bg: 'bg-violet-50 dark:bg-violet-950', text: 'text-violet-700 dark:text-violet-400', dot: 'bg-violet-500' },
  COMMITTING: { bg: 'bg-indigo-50 dark:bg-indigo-950', text: 'text-indigo-700 dark:text-indigo-400', dot: 'bg-indigo-500' },
  REVEALING: { bg: 'bg-cyan-50 dark:bg-cyan-950', text: 'text-cyan-700 dark:text-cyan-400', dot: 'bg-cyan-500' },
  ACCEPTED: { bg: 'bg-green-50 dark:bg-green-950', text: 'text-green-700 dark:text-green-400', dot: 'bg-green-500' },
  FINALIZED: { bg: 'bg-emerald-50 dark:bg-emerald-950', text: 'text-emerald-700 dark:text-emerald-400', dot: 'bg-emerald-500' },
  UNDETERMINED: { bg: 'bg-orange-50 dark:bg-orange-950', text: 'text-orange-700 dark:text-orange-400', dot: 'bg-orange-500' },
  LEADER_TIMEOUT: { bg: 'bg-red-50 dark:bg-red-950', text: 'text-red-700 dark:text-red-400', dot: 'bg-red-500' },
  VALIDATORS_TIMEOUT: { bg: 'bg-rose-50 dark:bg-rose-950', text: 'text-rose-700 dark:text-rose-400', dot: 'bg-rose-500' },
};

const defaultConfig = { bg: 'bg-muted', text: 'text-muted-foreground', dot: 'bg-muted-foreground' };

export function StatusBadge({ status }: { status: TransactionStatus }) {
  const config = statusConfig[status] || defaultConfig;

  return (
    <span className={cn('inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold', config.bg, config.text)}>
      <span className={cn('w-1.5 h-1.5 rounded-full', config.dot)}></span>
      {status.replace(/_/g, ' ')}
    </span>
  );
}
