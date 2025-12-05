'use client';

import { TransactionStatus } from '@/lib/types';

const statusConfig: Record<TransactionStatus, { bg: string; text: string; dot: string }> = {
  PENDING: { bg: 'bg-amber-50', text: 'text-amber-700', dot: 'bg-amber-500' },
  ACTIVATED: { bg: 'bg-blue-50', text: 'text-blue-700', dot: 'bg-blue-500' },
  CANCELED: { bg: 'bg-slate-100', text: 'text-slate-600', dot: 'bg-slate-400' },
  PROPOSING: { bg: 'bg-violet-50', text: 'text-violet-700', dot: 'bg-violet-500' },
  COMMITTING: { bg: 'bg-indigo-50', text: 'text-indigo-700', dot: 'bg-indigo-500' },
  REVEALING: { bg: 'bg-cyan-50', text: 'text-cyan-700', dot: 'bg-cyan-500' },
  ACCEPTED: { bg: 'bg-green-50', text: 'text-green-700', dot: 'bg-green-500' },
  FINALIZED: { bg: 'bg-emerald-50', text: 'text-emerald-700', dot: 'bg-emerald-500' },
  UNDETERMINED: { bg: 'bg-orange-50', text: 'text-orange-700', dot: 'bg-orange-500' },
  LEADER_TIMEOUT: { bg: 'bg-red-50', text: 'text-red-700', dot: 'bg-red-500' },
  VALIDATORS_TIMEOUT: { bg: 'bg-rose-50', text: 'text-rose-700', dot: 'bg-rose-500' },
};

const defaultConfig = { bg: 'bg-slate-100', text: 'text-slate-600', dot: 'bg-slate-400' };

export function StatusBadge({ status }: { status: TransactionStatus }) {
  const config = statusConfig[status] || defaultConfig;

  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold ${config.bg} ${config.text}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${config.dot}`}></span>
      {status.replace(/_/g, ' ')}
    </span>
  );
}
