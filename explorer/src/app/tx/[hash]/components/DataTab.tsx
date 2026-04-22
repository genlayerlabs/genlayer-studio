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
  const dataObj =
    tx.data && typeof tx.data === 'object' ? (tx.data as Record<string, unknown>) : null;
  const calldataB64 =
    (tx.type === 1 || tx.type === 2) && dataObj
      ? (dataObj.calldata as string | undefined)
      : undefined;
  const contractCodeB64 =
    tx.type === 1 && dataObj ? (dataObj.contract_code as string | undefined) : undefined;

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

      {/* Deploy transactions: show contract source code alongside constructor args.
          DataDecodePanel renders each base64 field with the best decoder per key
          (CodeBlock for contract_code, calldata decoder for calldata). */}
      {contractCodeB64 && dataObj && (
        <div>
          <h4 className="font-medium text-foreground mb-2 flex items-center gap-2">
            <FileCode className="w-4 h-4" />
            Contract Source
          </h4>
          <DataDecodePanel data={dataObj} />
        </div>
      )}

      {/* Call transactions: focused Etherscan-style decoder on calldata. */}
      {calldataB64 && !contractCodeB64 && (
        <div>
          <h4 className="font-medium text-foreground mb-2 flex items-center gap-2">
            <FileCode className="w-4 h-4" />
            Input Data
          </h4>
          <InputDataPanel calldataB64={calldataB64} />
        </div>
      )}

      {/* Fallback for non-call/non-deploy transactions that still carry data. */}
      {tx.data && !calldataB64 && !contractCodeB64 && (
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
