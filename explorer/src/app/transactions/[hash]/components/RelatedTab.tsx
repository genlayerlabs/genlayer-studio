'use client';

import Link from 'next/link';
import { format } from 'date-fns';
import { Transaction } from '@/lib/types';
import { StatusBadge } from '@/components/StatusBadge';
import { Card } from '@/components/ui/card';
import { ArrowLeft, Link as LinkIcon } from 'lucide-react';

interface RelatedTabProps {
  parentTransaction: Transaction | null;
  triggeredTransactions: Transaction[];
}

export function RelatedTab({ parentTransaction, triggeredTransactions }: RelatedTabProps) {
  return (
    <div className="space-y-6">
      {parentTransaction && (
        <div>
          <h4 className="font-medium text-foreground mb-3 flex items-center gap-2">
            <ArrowLeft className="w-4 h-4" />
            Parent Transaction
          </h4>
          <Link
            href={`/transactions/${parentTransaction.hash}`}
            className="block"
          >
            <Card className="p-4 bg-blue-50 dark:bg-blue-950 border-blue-200 dark:border-blue-800 hover:bg-blue-100 dark:hover:bg-blue-900 transition-colors">
              <div className="flex items-center justify-between">
                <code className="font-mono text-sm text-primary">
                  {parentTransaction.hash}
                </code>
                <StatusBadge status={parentTransaction.status} />
              </div>
            </Card>
          </Link>
        </div>
      )}

      {triggeredTransactions.length > 0 && (
        <div>
          <h4 className="font-medium text-foreground mb-3 flex items-center gap-2">
            <LinkIcon className="w-4 h-4" />
            Triggered Transactions ({triggeredTransactions.length})
          </h4>
          <div className="space-y-2">
            {triggeredTransactions.map((ttx) => (
              <Link
                key={ttx.hash}
                href={`/transactions/${ttx.hash}`}
                className="block"
              >
                <Card className="p-4 hover:bg-accent transition-colors">
                  <div className="flex items-center justify-between">
                    <code className="font-mono text-sm text-muted-foreground">
                      {ttx.hash}
                    </code>
                    <StatusBadge status={ttx.status} />
                  </div>
                  <div className="text-sm text-muted-foreground mt-2">
                    {ttx.created_at && format(new Date(ttx.created_at), 'PPpp')}
                  </div>
                </Card>
              </Link>
            ))}
          </div>
        </div>
      )}

      {!parentTransaction && triggeredTransactions.length === 0 && (
        <div className="text-muted-foreground italic text-center py-8">
          No related transactions found
        </div>
      )}
    </div>
  );
}
