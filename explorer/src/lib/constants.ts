import { TransactionStatus } from "./types";

/**
 * All possible transaction statuses in order of workflow
 */
export const TRANSACTION_STATUS_OPTIONS: TransactionStatus[] = [
  "PENDING",
  "ACTIVATED",
  "CANCELED",
  "PROPOSING",
  "COMMITTING",
  "REVEALING",
  "ACCEPTED",
  "FINALIZED",
  "UNDETERMINED",
  "LEADER_TIMEOUT",
  "VALIDATORS_TIMEOUT",
];

/**
 * Transaction statuses in display order (most common/important first)
 */
export const TRANSACTION_STATUS_DISPLAY_ORDER: TransactionStatus[] = [
  "FINALIZED",
  "ACCEPTED",
  "PENDING",
  "ACTIVATED",
  "PROPOSING",
  "COMMITTING",
  "REVEALING",
  "UNDETERMINED",
  "LEADER_TIMEOUT",
  "VALIDATORS_TIMEOUT",
  "CANCELED",
];

/**
 * Page size options for pagination
 */
export const PAGE_SIZE_OPTIONS = [20, 40, 60, 80, 100] as const;

/**
 * Default page size
 */
export const DEFAULT_PAGE_SIZE = 20;

/**
 * Transaction type labels and colors
 */
export const TRANSACTION_TYPES = {
  DEPLOY: {
    type0: {
      label: "Deploy",
      bgColor: "bg-blue-50",
      textColor: "text-blue-700",
    },
    type1: {
      label: "Deploy",
      bgColor: "bg-orange-50",
      textColor: "text-orange-700",
    },
    type2: {
      label: "Deploy",
      bgColor: "bg-orange-50",
      textColor: "text-orange-700",
    },
    type3: {
      label: "Upgrade",
      bgColor: "bg-slate-100",
      textColor: "text-slate-600",
    },
  },
  CALL: {
    type1: {
      label: "Call",
      bgColor: "bg-emerald-50",
      textColor: "text-emerald-700",
    },
    type2: {
      label: "Call",
      bgColor: "bg-violet-50",
      textColor: "text-violet-700",
    },
    type3: {
      label: "Upgrade",
      bgColor: "bg-slate-100",
      textColor: "text-slate-600",
    },
  },
  UNKNOWN: {
    label: "Unknown",
    bgColor: "bg-slate-100",
    textColor: "text-slate-600",
  },
} as const;
