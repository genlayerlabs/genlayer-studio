'use client';

import { HelpCircle } from 'lucide-react';
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
  TooltipProvider,
} from '@/components/ui/tooltip';

/** Shared tooltip descriptions for GenLayer protocol columns */
export const COLUMN_TOOLTIPS = {
  status: 'Transaction lifecycle state in the GenLayer protocol: PENDING → PROPOSING → COMMITTING → REVEALING → ACCEPTED → FINALIZED',
  genvmResult: 'Execution outcome from the GenLayer Virtual Machine: SUCCESS means the contract code ran without errors',
  consensusResult: 'Outcome of the validator consensus round: Accepted (validators agree), Leader Rotation, Majority Disagree, Undetermined, or timeout',
} as const;

interface ColumnHeaderWithTooltipProps {
  label: string;
  tooltip: string;
}

export function ColumnHeaderWithTooltip({ label, tooltip }: ColumnHeaderWithTooltipProps) {
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex items-center gap-1 cursor-help">
            {label}
            <HelpCircle className="w-3 h-3 text-muted-foreground/80 hover:text-muted-foreground transition-colors" />
          </span>
        </TooltipTrigger>
        <TooltipContent side="right" className="max-w-56">
          <p className="leading-relaxed">{tooltip}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
