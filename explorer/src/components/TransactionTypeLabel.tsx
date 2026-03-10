"use client";

import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

interface TransactionTypeLabelProps {
  type: number | null;
}

const typeConfig: Record<number, { label: string; className: string }> = {
  0: { label: 'Send', className: 'bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-950 dark:text-blue-400 dark:border-blue-800' },
  1: { label: 'Deploy', className: 'bg-orange-50 text-orange-700 border-orange-200 dark:bg-orange-950 dark:text-orange-400 dark:border-orange-800' },
  2: { label: 'Call', className: 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950 dark:text-emerald-400 dark:border-emerald-800' },
  3: { label: 'Upgrade', className: 'bg-violet-50 text-violet-700 border-violet-200 dark:bg-violet-950 dark:text-violet-400 dark:border-violet-800' },
};

const defaultType = { label: 'Unknown', className: 'bg-muted text-muted-foreground' };

export function TransactionTypeLabel({ type }: TransactionTypeLabelProps) {
  const config = type !== null && typeConfig[type] ? typeConfig[type] : defaultType;

  return (
    <Badge variant="outline" className={cn(config.className)}>
      {config.label}
    </Badge>
  );
}
