'use client';

import { Transaction } from '@/lib/types';
import { JsonViewer } from '@/components/JsonViewer';
import { User, FileCode } from 'lucide-react';

interface DataTabProps {
  transaction: Transaction;
}

export function DataTab({ transaction: tx }: DataTabProps) {
  return (
    <div className="space-y-6">
      {tx.input_data && (
        <div>
          <h4 className="font-medium text-gray-700 mb-2 flex items-center gap-2">
            <User className="w-4 h-4" />
            Input Data
          </h4>
          <div className="bg-gray-50 p-4 rounded-lg overflow-auto max-h-96">
            <JsonViewer data={tx.input_data} />
          </div>
        </div>
      )}

      {tx.data && (
        <div>
          <h4 className="font-medium text-gray-700 mb-2 flex items-center gap-2">
            <FileCode className="w-4 h-4" />
            Transaction Data
          </h4>
          <div className="bg-gray-50 p-4 rounded-lg overflow-auto max-h-96">
            <JsonViewer data={tx.data} />
          </div>
        </div>
      )}

      {tx.contract_snapshot && (
        <div>
          <h4 className="font-medium text-gray-700 mb-2">Contract Snapshot</h4>
          <div className="bg-gray-50 p-4 rounded-lg overflow-auto max-h-96">
            <JsonViewer data={tx.contract_snapshot} />
          </div>
        </div>
      )}

      {/* Signature */}
      {(tx.r !== null || tx.s !== null || tx.v !== null) && (
        <div>
          <h4 className="font-medium text-gray-700 mb-2">Signature</h4>
          <div className="bg-gray-50 p-4 rounded-lg space-y-2 font-mono text-sm">
            {tx.r !== null && <div><span className="text-gray-500">r:</span> {tx.r}</div>}
            {tx.s !== null && <div><span className="text-gray-500">s:</span> {tx.s}</div>}
            {tx.v !== null && <div><span className="text-gray-500">v:</span> {tx.v}</div>}
          </div>
        </div>
      )}
    </div>
  );
}
