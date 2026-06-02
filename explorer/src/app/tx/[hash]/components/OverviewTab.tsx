'use client';

import Link from '@/components/AppLink';
import { format } from 'date-fns';
import { Transaction } from '@/lib/types';
import { StatusBadge } from '@/components/StatusBadge';
import { TransactionTypeLabel } from '@/components/TransactionTypeLabel';
import { ConsensusJourney } from '@/components/ConsensusJourney';
import { InfoRow } from '@/components/InfoRow';
import { Badge } from '@/components/ui/badge';
import { JsonViewer } from '@/components/JsonViewer';
import { getExecutionResult, getConsensusRoundResult } from '@/lib/transactionUtils';
import { ConsensusResultBadge } from '@/components/ConsensusResultBadge';
import { resultStatusLabel, type DecodedResult } from '@/lib/resultDecoder';
import { InputDataPanel } from '@/components/InputDataPanel';
import { DataDecodePanel } from '@/components/DataDecodePanel';
import { formatGenValue } from '@/lib/formatters';

interface OverviewTabProps {
  transaction: Transaction;
}

function ResultStatusBadge({ status }: { status: string }) {
  const label = resultStatusLabel(status);
  switch (status) {
    case 'return':
      return (
        <Badge className="bg-green-50 dark:bg-green-950 text-green-700 dark:text-green-400 border-green-200 dark:border-green-800">
          {label}
        </Badge>
      );
    case 'rollback':
      return (
        <Badge className="bg-orange-50 dark:bg-orange-950 text-orange-700 dark:text-orange-400 border-orange-200 dark:border-orange-800">
          {label}
        </Badge>
      );
    case 'contract_error':
    case 'error':
      return (
        <Badge className="bg-red-50 dark:bg-red-950 text-red-700 dark:text-red-400 border-red-200 dark:border-red-800">
          {label}
        </Badge>
      );
    case 'none':
    case 'no_leaders':
      return (
        <Badge className="bg-gray-50 dark:bg-gray-950 text-gray-700 dark:text-gray-400 border-gray-200 dark:border-gray-800">
          {label}
        </Badge>
      );
    default:
      return <Badge variant="outline">{label}</Badge>;
  }
}

function ResultPayload({ decoded }: { decoded: DecodedResult }) {
  if (decoded.payload === null || decoded.payload === undefined) return null;

  // Calldata-decoded payload (has .readable)
  if (
    typeof decoded.payload === 'object' &&
    decoded.payload !== null &&
    'readable' in (decoded.payload as Record<string, unknown>)
  ) {
    const readable = (decoded.payload as { readable: string }).readable;
    return (
      <div className="bg-muted p-3 rounded-lg mt-2">
        <div className="text-xs text-muted-foreground mb-1">Return Value</div>
        <code className="text-sm text-foreground break-all">{readable}</code>
      </div>
    );
  }

  // String payload (error message from rollback/contract_error)
  if (typeof decoded.payload === 'string') {
    return (
      <div className="bg-red-50 dark:bg-red-950/50 border border-red-200 dark:border-red-800 p-3 rounded-lg mt-2">
        <div className="text-xs text-red-600 dark:text-red-400 mb-1">
          {decoded.status === 'rollback' || decoded.status === 'contract_error'
            ? 'Error Message'
            : 'Payload'}
        </div>
        <code className="text-sm text-red-800 dark:text-red-300 break-all">
          {decoded.payload}
        </code>
      </div>
    );
  }

  return null;
}

export function OverviewTab({ transaction: tx }: OverviewTabProps) {
  const execResult = getExecutionResult(tx);
  const executionResult = execResult?.executionResult;
  const genvmResult = execResult?.genvmResult;
  const consensusRound = getConsensusRoundResult(tx);
  const decodedResult = execResult?.decodedResult;
  const eqOutputs = execResult?.eqOutputs;

  const dataObj =
    tx.data && typeof tx.data === 'object' ? (tx.data as Record<string, unknown>) : null;
  const calldataB64 =
    (tx.type === 1 || tx.type === 2) && dataObj
      ? (dataObj.calldata as string | undefined)
      : undefined;
  const contractCodeB64 =
    tx.type === 1 && dataObj ? (dataObj.contract_code as string | undefined) : undefined;

  return (
    <div className="space-y-1">
      <InfoRow label="Hash" value={tx.hash} copyable />
      <InfoRow label="Status" value={<StatusBadge status={tx.status} />} />
      <InfoRow label="Type" value={<TransactionTypeLabel type={tx.type} />} />
      <InfoRow
        label="From"
        value={
          tx.from_address ? (
            <Link href={`/address/${tx.from_address}`} className="text-primary hover:underline">
              {tx.from_address}
            </Link>
          ) : (
            '-'
          )
        }
        copyable={!!tx.from_address}
        copyText={tx.from_address || undefined}
      />
      <InfoRow
        label="To"
        value={
          tx.to_address ? (
            <Link href={`/address/${tx.to_address}`} className="text-primary hover:underline">
              {tx.to_address}
            </Link>
          ) : (
            '-'
          )
        }
        copyable={!!tx.to_address}
        copyText={tx.to_address || undefined}
      />
      <InfoRow label="Value" value={formatGenValue(tx.value)} />
      <InfoRow label="Nonce" value={tx.nonce !== null ? tx.nonce.toString() : '-'} />
      <InfoRow label="Gas Limit" value={tx.gaslimit !== null ? tx.gaslimit.toString() : '-'} />
      <InfoRow
        label="Created At"
        value={tx.created_at ? format(new Date(tx.created_at), 'PPpp') : '-'}
      />
      <InfoRow
        label="Execution Mode"
        value={
          tx.execution_mode === 'LEADER_ONLY' ? (
            <Badge className="bg-yellow-50 dark:bg-yellow-950 text-yellow-700 dark:text-yellow-400 border-yellow-200 dark:border-yellow-800">Leader Only</Badge>
          ) : tx.execution_mode === 'LEADER_SELF_VALIDATOR' ? (
            <Badge className="bg-blue-50 dark:bg-blue-950 text-blue-700 dark:text-blue-400 border-blue-200 dark:border-blue-800">Leader + Self Validator</Badge>
          ) : (
            <Badge className="bg-green-50 dark:bg-green-950 text-green-700 dark:text-green-400 border-green-200 dark:border-green-800">Normal</Badge>
          )
        }
      />
      <InfoRow label="Rotation Count" value={tx.rotation_count?.toString() || '-'} />
      <InfoRow label="Initial Validators" value={tx.num_of_initial_validators?.toString() || '-'} />
      <InfoRow
        label="Consensus Result"
        value={consensusRound ? <ConsensusResultBadge result={consensusRound} variant="badge" /> : '-'}
      />
      {tx.worker_id && <InfoRow label="Worker ID" value={tx.worker_id} />}

      {contractCodeB64 && dataObj && (
        <div className="border-t border-border mt-4 pt-4">
          <h4 className="text-sm font-semibold text-foreground mb-3">Input Data</h4>
          {/* Deploy: show both constructor calldata and contract source */}
          <DataDecodePanel data={dataObj} />
        </div>
      )}

      {calldataB64 && !contractCodeB64 && (
        <div className="border-t border-border mt-4 pt-4">
          <h4 className="text-sm font-semibold text-foreground mb-3">Input Data</h4>
          <InputDataPanel calldataB64={calldataB64} />
        </div>
      )}

      {/* Execution Result Section */}
      {(executionResult || genvmResult || decodedResult) && (
        <>
          <div className="border-t border-border mt-4 pt-4">
            <h4 className="text-sm font-semibold text-foreground mb-3">GenVM Execution</h4>
          </div>
          {executionResult && (
            <InfoRow
              label="Execution Result"
              value={
                executionResult === 'SUCCESS' ? (
                  <Badge className="bg-green-50 dark:bg-green-950 text-green-700 dark:text-green-400 border-green-200 dark:border-green-800">SUCCESS</Badge>
                ) : (
                  <Badge className="bg-red-50 dark:bg-red-950 text-red-700 dark:text-red-400 border-red-200 dark:border-red-800">{executionResult}</Badge>
                )
              }
            />
          )}
          {decodedResult && (
            <>
              <InfoRow
                label="Result Code"
                value={<ResultStatusBadge status={decodedResult.status} />}
              />
              <ResultPayload decoded={decodedResult} />
            </>
          )}
          {genvmResult?.stdout !== undefined && (
            <InfoRow
              label="Stdout"
              value={genvmResult.stdout || <span className="text-muted-foreground">(empty)</span>}
              copyable={!!genvmResult.stdout}
              copyText={genvmResult.stdout}
            />
          )}
          {genvmResult?.stderr !== undefined && (
            <InfoRow
              label="Stderr"
              value={
                genvmResult.stderr ? (
                  <span className="text-destructive">{genvmResult.stderr}</span>
                ) : (
                  <span className="text-muted-foreground">(empty)</span>
                )
              }
              copyable={!!genvmResult.stderr}
              copyText={genvmResult.stderr}
            />
          )}
        </>
      )}

      {/* Equivalence Principle Outputs */}
      {eqOutputs && Object.keys(eqOutputs).length > 0 && (
        <>
          <div className="border-t border-border mt-4 pt-4">
            <h4 className="text-sm font-semibold text-foreground mb-3">
              Equivalence Principle Outputs
            </h4>
          </div>
          <div className="space-y-3">
            {Object.entries(eqOutputs).map(([key, decoded]) => (
              <div key={key} className="bg-muted rounded-lg p-3">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-sm font-medium text-foreground">{key}</span>
                  <ResultStatusBadge status={decoded.status} />
                </div>
                {decoded.payload != null && (
                  <div className="text-sm">
                    {typeof decoded.payload === 'object' &&
                    decoded.payload !== null &&
                    'readable' in (decoded.payload as Record<string, unknown>) ? (
                      <code className="text-foreground break-all">
                        {(decoded.payload as { readable: string }).readable}
                      </code>
                    ) : typeof decoded.payload === 'string' ? (
                      <code className="text-foreground break-all">{decoded.payload}</code>
                    ) : (
                      <JsonViewer data={decoded.payload} />
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {/* Transaction Journey */}
      <div className="border-t border-border mt-4 pt-4">
        <ConsensusJourney transaction={tx} />
      </div>
    </div>
  );
}
