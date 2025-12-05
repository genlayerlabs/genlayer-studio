'use client';

import { isContractDeploy } from '@/lib/transactionUtils';

interface TransactionTypeLabelProps {
  type: number | null;
  contractSnapshot?: Record<string, unknown> | null;
}

export function TransactionTypeLabel({ type, contractSnapshot }: TransactionTypeLabelProps) {
  // Check if this is a contract deployment (type 1 with contract_code in snapshot)
  const isDeploy = type === 1 && isContractDeploy(contractSnapshot ?? null);

  if (isDeploy) {
    return (
      <span className="bg-orange-50 text-orange-700 px-2.5 py-1 rounded-lg text-xs font-semibold">
        Deploy
      </span>
    );
  }

  switch (type) {
    case 0:
      return (
        <span className="bg-blue-50 text-blue-700 px-2.5 py-1 rounded-lg text-xs font-semibold">
          Deploy
        </span>
      );
    case 1:
      return (
        <span className="bg-emerald-50 text-emerald-700 px-2.5 py-1 rounded-lg text-xs font-semibold">
          Call
        </span>
      );
    case 2:
      return (
        <span className="bg-violet-50 text-violet-700 px-2.5 py-1 rounded-lg text-xs font-semibold">
          Call
        </span>
      );
    default:
      return (
        <span className="bg-slate-100 text-slate-600 px-2.5 py-1 rounded-lg text-xs font-semibold">
          Unknown
        </span>
      );
  }
}
