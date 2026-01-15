"use client";

interface TransactionTypeLabelProps {
  type: number | null;
}

export function TransactionTypeLabel({ type }: TransactionTypeLabelProps) {
  // Transaction types from backend/domain/types.py:
  // 0 = SEND
  // 1 = DEPLOY_CONTRACT
  // 2 = RUN_CONTRACT
  // 3 = UPGRADE_CONTRACT

  switch (type) {
    case 0:
      return (
        <span className="bg-blue-50 text-blue-700 px-2.5 py-1 rounded-lg text-xs font-semibold">
          Send
        </span>
      );
    case 1:
      return (
        <span className="bg-orange-50 text-orange-700 px-2.5 py-1 rounded-lg text-xs font-semibold">
          Deploy
        </span>
      );
    case 2:
      return (
        <span className="bg-emerald-50 text-emerald-700 px-2.5 py-1 rounded-lg text-xs font-semibold">
          Call
        </span>
      );
    case 3:
      return (
        <span className="bg-violet-50 text-violet-700 px-2.5 py-1 rounded-lg text-xs font-semibold">
          Upgrade
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
