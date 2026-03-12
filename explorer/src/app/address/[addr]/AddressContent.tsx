'use client';

import Link from 'next/link';
import { formatDistanceToNow, format } from 'date-fns';

import { Transaction, Validator, CurrentState } from '@/lib/types';
import { AddressTransactionTable } from '@/components/AddressTransactionTable';
import { CopyButton } from '@/components/CopyButton';
import { CodeBlock } from '@/components/CodeBlock';
import { JsonViewer } from '@/components/JsonViewer';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { formatGenValue, truncateAddress } from '@/lib/formatters';
import {
  ArrowLeft,
  Wallet,
  Users,
  ArrowRightLeft,
  Cpu,
  Settings,
  Clock,
  Database,
  FileCode,
} from 'lucide-react';

interface CreatorInfo {
  creator_address: string | null;
  deployment_tx_hash: string;
  creation_timestamp: string | null;
}

export interface AddressInfo {
  type: 'CONTRACT' | 'VALIDATOR' | 'ACCOUNT';
  address: string;
  validator?: Validator;
  balance?: number;
  tx_count?: number;
  first_tx_time?: string | null;
  last_tx_time?: string | null;
  transactions?: Transaction[];
  state?: CurrentState;
  contract_code?: string | null;
  creator_info?: CreatorInfo | null;
}

export function AddressContent({ addr, data }: { addr: string; data: AddressInfo }) {
  if (data.type === 'CONTRACT') return <ContractView address={addr} data={data} />;
  if (data.type === 'VALIDATOR' && data.validator) return <ValidatorView address={addr} validator={data.validator} />;
  return <AccountView address={addr} data={data} />;
}

// ---------------------------------------------------------------------------
// Account
// ---------------------------------------------------------------------------

function AccountView({ address, data }: { address: string; data: AddressInfo }) {
  const txs = data.transactions || [];

  return (
    <div className="space-y-6">
      <AddressHeader title="Account" address={address} backHref="/" icon={<Wallet className="w-8 h-8 text-blue-600 dark:text-blue-400" />} iconBg="bg-blue-100 dark:bg-blue-950" />

      <Card>
        <CardContent className="p-6">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            <StatItem icon={<Wallet className="w-5 h-5 text-green-600 dark:text-green-400" />} iconBg="bg-green-100 dark:bg-green-950" label="Balance" value={formatGenValue(data.balance ?? 0)} />
            <StatItem icon={<ArrowRightLeft className="w-5 h-5 text-blue-600 dark:text-blue-400" />} iconBg="bg-blue-100 dark:bg-blue-950" label="Transactions" value={String(data.tx_count ?? txs.length)} />
            <div>
              <p className="text-muted-foreground text-sm mb-1">First Tx</p>
              <p className="text-sm text-foreground">
                {data.first_tx_time ? formatDistanceToNow(new Date(data.first_tx_time), { addSuffix: true }) : '-'}
              </p>
            </div>
            <div>
              <p className="text-muted-foreground text-sm mb-1">Latest Tx</p>
              <p className="text-sm text-foreground">
                {data.last_tx_time ? formatDistanceToNow(new Date(data.last_tx_time), { addSuffix: true }) : '-'}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      <Tabs defaultValue="transactions">
        <TabsList>
          <TabsTrigger value="transactions" className="flex items-center gap-1.5">
            <ArrowRightLeft className="w-4 h-4" />
            Transactions ({txs.length})
          </TabsTrigger>
        </TabsList>
        <TabsContent value="transactions">
          <Card className="overflow-hidden">
            <AddressTransactionTable transactions={txs} address={address} />
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Contract
// ---------------------------------------------------------------------------

function ContractView({ address, data }: { address: string; data: AddressInfo }) {
  const state = data.state;
  const transactions = data.transactions || [];
  const contract_code = data.contract_code;
  const creator_info = data.creator_info;

  return (
    <div className="space-y-6">
      <AddressHeader title="Contract" address={address} backHref="/contracts" icon={<Database className="w-8 h-8 text-purple-600 dark:text-purple-400" />} iconBg="bg-purple-100 dark:bg-purple-950" />

      <Card>
        <CardContent className="p-6">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {state && (
              <>
                <StatItem icon={<Wallet className="w-5 h-5 text-green-600 dark:text-green-400" />} iconBg="bg-green-100 dark:bg-green-950" label="Balance" value={formatGenValue(state.balance)} />
                <StatItem icon={<ArrowRightLeft className="w-5 h-5 text-blue-600 dark:text-blue-400" />} iconBg="bg-blue-100 dark:bg-blue-950" label="Transactions" value={String(data.tx_count ?? transactions.length)} />
                <StatItem icon={<Clock className="w-5 h-5 text-muted-foreground" />} iconBg="bg-muted" label="Last Updated" value={state.updated_at ? formatDistanceToNow(new Date(state.updated_at), { addSuffix: true }) : 'Unknown'} small />
              </>
            )}
            {creator_info && (
              <>
                <div>
                  <p className="text-muted-foreground text-sm mb-1">Creator</p>
                  {creator_info.creator_address ? (
                    <div className="flex items-center gap-1">
                      <Link href={`/address/${creator_info.creator_address}`} className="text-primary hover:underline font-mono text-sm">
                        {truncateAddress(creator_info.creator_address)}
                      </Link>
                      <CopyButton text={creator_info.creator_address} />
                    </div>
                  ) : <span className="text-muted-foreground">-</span>}
                </div>
                <div>
                  <p className="text-muted-foreground text-sm mb-1">Deploy Tx</p>
                  <div className="flex items-center gap-1">
                    <Link href={`/transactions/${creator_info.deployment_tx_hash}`} className="text-primary hover:underline font-mono text-sm">
                      {creator_info.deployment_tx_hash.slice(0, 10)}...{creator_info.deployment_tx_hash.slice(-8)}
                    </Link>
                    <CopyButton text={creator_info.deployment_tx_hash} />
                  </div>
                </div>
                <div>
                  <p className="text-muted-foreground text-sm mb-1">Created</p>
                  <p className="text-sm text-foreground">
                    {creator_info.creation_timestamp ? format(new Date(creator_info.creation_timestamp), 'PPpp') : '-'}
                  </p>
                </div>
              </>
            )}
          </div>
        </CardContent>
      </Card>

      <Tabs defaultValue="transactions">
        <TabsList>
          <TabsTrigger value="transactions" className="flex items-center gap-1.5">
            <ArrowRightLeft className="w-4 h-4" />
            Transactions ({transactions.length})
          </TabsTrigger>
          {contract_code && (
            <TabsTrigger value="code" className="flex items-center gap-1.5">
              <FileCode className="w-4 h-4" />
              Contract
            </TabsTrigger>
          )}
          {state?.data && Object.keys(state.data).length > 0 && (
            <TabsTrigger value="state" className="flex items-center gap-1.5">
              <Database className="w-4 h-4" />
              State
            </TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="transactions">
          <Card className="overflow-hidden">
            <AddressTransactionTable transactions={transactions} address={address} />
          </Card>
        </TabsContent>

        {contract_code && (
          <TabsContent value="code">
            <Card>
              <CardContent className="p-6">
                <CodeBlock code={contract_code} />
              </CardContent>
            </Card>
          </TabsContent>
        )}

        {state?.data && Object.keys(state.data).length > 0 && (
          <TabsContent value="state">
            <Card>
              <CardContent className="p-6">
                <div className="bg-muted p-4 rounded-lg overflow-auto max-h-[500px]">
                  <JsonViewer data={state.data} />
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Validator
// ---------------------------------------------------------------------------

function ValidatorView({ address, validator }: { address: string; validator: Validator }) {
  return (
    <div className="space-y-6">
      <AddressHeader title="Validator" address={address} backHref="/validators" icon={<Users className="w-8 h-8 text-emerald-600 dark:text-emerald-400" />} iconBg="bg-emerald-100 dark:bg-emerald-950" />

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card>
          <CardContent className="p-6">
            <StatItem icon={<Wallet className="w-5 h-5 text-green-600 dark:text-green-400" />} iconBg="bg-green-100 dark:bg-green-950" label="Stake" value={formatGenValue(validator.stake)} />
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-6">
            <div className="flex items-center gap-3">
              <div className="bg-blue-100 dark:bg-blue-950 p-2 rounded-lg">
                <Cpu className="w-5 h-5 text-blue-600 dark:text-blue-400" />
              </div>
              <div>
                <p className="text-muted-foreground text-sm">Provider / Model</p>
                <div className="flex items-center gap-2 mt-1">
                  <Badge variant="secondary">{validator.provider}</Badge>
                  <Badge variant="outline">{validator.model}</Badge>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-6">
            <StatItem icon={<Clock className="w-5 h-5 text-muted-foreground" />} iconBg="bg-muted" label="Created" value={validator.created_at ? formatDistanceToNow(new Date(validator.created_at), { addSuffix: true }) : 'Unknown'} small />
          </CardContent>
        </Card>
      </div>

      {validator.config && Object.keys(validator.config).length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Settings className="w-5 h-5 text-muted-foreground" />
              Configuration
            </CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="bg-muted p-4 rounded-lg overflow-auto text-sm font-mono">
              {JSON.stringify(validator.config, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared primitives
// ---------------------------------------------------------------------------

function AddressHeader({ title, address, backHref, icon, iconBg }: {
  title: string;
  address: string;
  backHref: string;
  icon: React.ReactNode;
  iconBg: string;
}) {
  return (
    <div>
      <Button variant="ghost" size="sm" asChild className="mb-4">
        <Link href={backHref} className="flex items-center gap-2">
          <ArrowLeft className="w-4 h-4" />
          Back to {backHref.replace('/', '') || 'dashboard'}
        </Link>
      </Button>
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">{title}</h1>
          <div className="flex items-center gap-3 mt-2">
            <code className="font-mono text-sm text-muted-foreground">{address}</code>
            <CopyButton text={address} iconSize="md" />
          </div>
        </div>
        <div className={`${iconBg} p-3 rounded-lg`}>{icon}</div>
      </div>
    </div>
  );
}

function StatItem({ icon, iconBg, label, value, small }: {
  icon: React.ReactNode;
  iconBg: string;
  label: string;
  value: string;
  small?: boolean;
}) {
  return (
    <div className="flex items-center gap-3">
      <div className={`${iconBg} p-2 rounded-lg`}>{icon}</div>
      <div>
        <p className="text-muted-foreground text-sm">{label}</p>
        {small
          ? <p className="text-sm font-medium text-foreground">{value}</p>
          : <p className="text-xl font-bold text-foreground">{value}</p>
        }
      </div>
    </div>
  );
}
