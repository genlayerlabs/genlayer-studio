'use client';

import { useEffect, useState, use } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Transaction } from '@/lib/types';
import { StatusBadge } from '@/components/StatusBadge';
import { TransactionTypeLabel } from '@/components/TransactionTypeLabel';
import { CopyButton } from '@/components/CopyButton';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  OverviewTab,
  MonitoringTab,
  ConsensusTab,
  DataTab,
  RelatedTab,
} from './components';
import {
  ArrowLeft,
  Clock,
  AlertTriangle,
  Link as LinkIcon,
  Loader2,
  FileCode,
  Hash,
  Cpu,
  Activity,
  Trash2,
  Edit2,
} from 'lucide-react';
import { TransactionStatus } from '@/lib/types';

interface TransactionDetail {
  transaction: Transaction;
  triggeredTransactions: Transaction[];
  parentTransaction: Transaction | null;
}

const TABS = [
  { id: 'overview', label: 'Overview', icon: Hash },
  { id: 'monitoring', label: 'Monitoring', icon: Activity },
  { id: 'consensus', label: 'Consensus', icon: Cpu },
  { id: 'data', label: 'Data', icon: FileCode },
  { id: 'related', label: 'Related', icon: LinkIcon },
];

export default function TransactionDetailPage({ params }: { params: Promise<{ hash: string }> }) {
  const { hash } = use(params);
  const router = useRouter();
  const [data, setData] = useState<TransactionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [updatingStatus, setUpdatingStatus] = useState(false);
  const [showStatusEdit, setShowStatusEdit] = useState(false);

  useEffect(() => {
    async function fetchTransaction() {
      try {
        const res = await fetch(`/api/transactions/${hash}`);
        if (!res.ok) {
          if (res.status === 404) throw new Error('Transaction not found');
          throw new Error('Failed to fetch transaction');
        }
        const data = await res.json();
        setData(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    }
    fetchTransaction();
  }, [hash]);

  const handleDelete = async () => {
    setDeleting(true);
    try {
      const res = await fetch(`/api/transactions/${hash}`, {
        method: 'DELETE',
      });

      if (!res.ok) {
        const errorData = await res.json();
        throw new Error(errorData.error || 'Failed to delete transaction');
      }

      router.push('/transactions');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete transaction');
      setDeleting(false);
      setShowDeleteConfirm(false);
    }
  };

  const handleStatusUpdate = async (newStatus: TransactionStatus) => {
    setUpdatingStatus(true);
    setError(null);
    try {
      const res = await fetch(`/api/transactions/${hash}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ status: newStatus }),
      });

      if (!res.ok) {
        const errorData = await res.json();
        throw new Error(errorData.error || 'Failed to update transaction status');
      }

      const fetchRes = await fetch(`/api/transactions/${hash}`);
      if (fetchRes.ok) {
        const updatedData = await fetchRes.json();
        setData(updatedData);
      }
      setShowStatusEdit(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update transaction status');
    } finally {
      setUpdatingStatus(false);
    }
  };

  const validStatuses: TransactionStatus[] = [
    'PENDING', 'ACTIVATED', 'CANCELED', 'PROPOSING', 'COMMITTING',
    'REVEALING', 'ACCEPTED', 'FINALIZED', 'UNDETERMINED',
    'LEADER_TIMEOUT', 'VALIDATORS_TIMEOUT',
  ];

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/transactions" className="flex items-center gap-2">
            <ArrowLeft className="w-4 h-4" />
            Back to transactions
          </Link>
        </Button>
        <Card className="border-destructive">
          <CardContent className="p-6">
            <h2 className="font-bold mb-2 text-destructive">Error</h2>
            <p className="text-destructive/80">{error}</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (!data) return null;

  const { transaction: tx, triggeredTransactions, parentTransaction } = data;
  const relatedCount = triggeredTransactions.length + (parentTransaction ? 1 : 0);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <Button variant="ghost" size="sm" asChild className="mb-4">
            <Link href="/transactions" className="flex items-center gap-2">
              <ArrowLeft className="w-4 h-4" />
              Back to transactions
            </Link>
          </Button>
          <h1 className="text-2xl font-bold text-foreground">Transaction Details</h1>
          <div className="flex items-center gap-3 mt-2">
            <code className="font-mono text-sm text-muted-foreground">{tx.hash}</code>
            <CopyButton text={tx.hash} iconSize="md" />
          </div>
        </div>
        <div className="flex flex-col items-end gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowDeleteConfirm(true)}
            className="text-destructive hover:text-destructive hover:bg-destructive/10"
          >
            <Trash2 className="w-4 h-4 mr-1" />
            Delete
          </Button>
          <div className="flex items-center gap-2">
            {showStatusEdit ? (
              <div className="flex items-center gap-2">
                <Select
                  value={tx.status}
                  onValueChange={(value) => handleStatusUpdate(value as TransactionStatus)}
                  disabled={updatingStatus}
                >
                  <SelectTrigger className="w-[180px] h-8">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {validStatuses.map((status) => (
                      <SelectItem key={status} value={status}>{status}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8"
                  onClick={() => setShowStatusEdit(false)}
                  disabled={updatingStatus}
                >
                  <span className="text-muted-foreground">✕</span>
                </Button>
                {updatingStatus && <Loader2 className="w-4 h-4 animate-spin text-primary" />}
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <StatusBadge status={tx.status} />
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6"
                  onClick={() => setShowStatusEdit(true)}
                  title="Update transaction status"
                >
                  <Edit2 className="w-3.5 h-3.5" />
                </Button>
              </div>
            )}
          </div>
          <TransactionTypeLabel type={tx.type} />
        </div>
      </div>

      {/* Delete Confirmation Dialog */}
      <Dialog open={showDeleteConfirm} onOpenChange={setShowDeleteConfirm}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Transaction</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete this transaction?
            </DialogDescription>
          </DialogHeader>
          <p className="text-sm text-muted-foreground font-mono break-all">{tx.hash}</p>
          {triggeredTransactions.length > 0 && (
            <Card className="border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950">
              <CardContent className="p-3">
                <p className="text-sm text-amber-800 dark:text-amber-300">
                  <strong>Note:</strong> This transaction has {triggeredTransactions.length} child transaction(s).
                  Their parent reference will be removed.
                </p>
              </CardContent>
            </Card>
          )}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setShowDeleteConfirm(false)}
              disabled={deleting}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleDelete}
              disabled={deleting}
            >
              {deleting ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin mr-1" />
                  Deleting...
                </>
              ) : (
                'Delete'
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Alert Badges */}
      <AlertBadges transaction={tx} />

      {/* Tabs */}
      <Tabs defaultValue="overview">
        <TabsList>
          {TABS.map((tab) => {
            const Icon = tab.icon;
            const label = tab.id === 'related' ? `Related (${relatedCount})` : tab.label;

            return (
              <TabsTrigger key={tab.id} value={tab.id} className="flex items-center gap-1.5">
                <Icon className="w-4 h-4" />
                {label}
              </TabsTrigger>
            );
          })}
        </TabsList>

        <Card className="mt-4">
          <CardContent className="p-6">
            <TabsContent value="overview"><OverviewTab transaction={tx} /></TabsContent>
            <TabsContent value="monitoring"><MonitoringTab transaction={tx} /></TabsContent>
            <TabsContent value="consensus"><ConsensusTab transaction={tx} /></TabsContent>
            <TabsContent value="data"><DataTab transaction={tx} /></TabsContent>
            <TabsContent value="related">
              <RelatedTab
                parentTransaction={parentTransaction}
                triggeredTransactions={triggeredTransactions}
              />
            </TabsContent>
          </CardContent>
        </Card>
      </Tabs>
    </div>
  );
}

function AlertBadges({ transaction: tx }: { transaction: Transaction }) {
  if (!tx.appealed && !tx.appeal_undetermined && !tx.appeal_leader_timeout && !tx.appeal_validators_timeout) {
    return null;
  }

  return (
    <div className="flex flex-wrap gap-2">
      {tx.appealed && (
        <div className="flex items-center gap-2 bg-orange-50 dark:bg-orange-950 text-orange-700 dark:text-orange-400 px-3 py-2 rounded-lg border border-orange-200 dark:border-orange-800">
          <AlertTriangle className="w-4 h-4" />
          Transaction was appealed
        </div>
      )}
      {tx.appeal_undetermined && (
        <div className="flex items-center gap-2 bg-yellow-50 dark:bg-yellow-950 text-yellow-700 dark:text-yellow-400 px-3 py-2 rounded-lg border border-yellow-200 dark:border-yellow-800">
          <AlertTriangle className="w-4 h-4" />
          Appeal undetermined
        </div>
      )}
      {tx.appeal_leader_timeout && (
        <div className="flex items-center gap-2 bg-red-50 dark:bg-red-950 text-red-700 dark:text-red-400 px-3 py-2 rounded-lg border border-red-200 dark:border-red-800">
          <Clock className="w-4 h-4" />
          Leader timeout on appeal
        </div>
      )}
      {tx.appeal_validators_timeout && (
        <div className="flex items-center gap-2 bg-red-50 dark:bg-red-950 text-red-700 dark:text-red-400 px-3 py-2 rounded-lg border border-red-200 dark:border-red-800">
          <Clock className="w-4 h-4" />
          Validators timeout on appeal
        </div>
      )}
    </div>
  );
}
