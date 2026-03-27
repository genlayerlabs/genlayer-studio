'use client';

import { Transaction } from '@/lib/types';
import { JsonViewer } from '@/components/JsonViewer';
import { DataDecodePanel } from '@/components/DataDecodePanel';
import { InputDataPanel } from '@/components/InputDataPanel';
import { User, FileCode } from 'lucide-react';

interface DataTabProps {
  transaction: Transaction;
}

export function DataTab({ transaction: tx }: DataTabProps) {
  const calldataB64 = (tx.type === 1 || tx.type === 2) && tx.data && typeof tx.data === 'object'
    ? (tx.data as Record<string, unknown>).calldata as string | undefined
    : undefined;

  return (
    <div className="space-y-6">
      {tx.input_data && (
        <div>
          <h4 className="font-medium text-foreground mb-2 flex items-center gap-2">
            <User className="w-4 h-4" />
            Input Data
          </h4>
          <div className="bg-muted p-4 rounded-lg overflow-auto max-h-96">
            <JsonViewer data={tx.input_data} />
          </div>
        </div>
      )}

      {/* Etherscan-style input data panel for call transactions */}
      {calldataB64 && (
        <div>
          <h4 className="font-medium text-foreground mb-2 flex items-center gap-2">
            <FileCode className="w-4 h-4" />
            Input Data
          </h4>
          <InputDataPanel calldataB64={calldataB64} />
        </div>
      )}

      {/* Generic data panel for non-call transactions */}
      {tx.data && !calldataB64 && (
        <div>
          <h4 className="font-medium text-foreground mb-2 flex items-center gap-2">
            <FileCode className="w-4 h-4" />
            Transaction Data
          </h4>
          <div className="bg-muted p-4 rounded-lg overflow-auto max-h-96">
            <DataDecodePanel data={tx.data} />
          </div>
        </div>
      )}

      {(tx.r !== null || tx.s !== null || tx.v !== null) && (
        <div>
          <h4 className="font-medium text-foreground mb-2">Signature</h4>
          <div className="bg-muted p-4 rounded-lg space-y-2 font-mono text-sm">
            {tx.r !== null && <div><span className="text-muted-foreground">r:</span> {tx.r}</div>}
            {tx.s !== null && <div><span className="text-muted-foreground">s:</span> {tx.s}</div>}
            {tx.v !== null && <div><span className="text-muted-foreground">v:</span> {tx.v}</div>}
          </div>
        </div>
      )}
    </div>
  );
}
