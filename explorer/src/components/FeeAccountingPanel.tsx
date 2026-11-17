'use client';

import type { Transaction } from '@/lib/types';
import {
  feeBucketRows,
  feeDistributionRows,
  feeMetricRows,
  feeRecommendedObservedRows,
  feeRecommendedPresetRows,
  formatFeeAmount,
  formatFeeParamsDecoded,
  formatInteger,
  getStudioFeeAccounting,
} from '@/lib/feeAccounting';
import { truncateAddress, truncateHash } from '@/lib/formatters';

interface FeeAccountingPanelProps {
  readonly transaction: Transaction;
}

export function FeeAccountingPanel({ transaction }: Readonly<FeeAccountingPanelProps>) {
  const accounting = getStudioFeeAccounting(transaction);
  if (!accounting) return null;

  const report = accounting.execution_fee_report;
  const messages = report?.messageReveal?.messages ?? [];
  const genvmBuckets = report?.genvmBuckets ?? accounting.genvm_fee_bucket_report;
  const messageFees = report?.messageFees;
  const executionMetering = report?.executionMetering;
  const metricRows = feeMetricRows(accounting);
  const distributionRows = feeDistributionRows(accounting);
  const recommendedRows = feeRecommendedPresetRows(accounting);
  const observedRows = feeRecommendedObservedRows(accounting);
  const chargeableBucketRows = feeBucketRows(report?.chargeableExecution);
  const genvmBucketRows = feeBucketRows(genvmBuckets);

  return (
    <div className="border-t border-border mt-4 pt-4 space-y-4">
      <h4 className="text-sm font-semibold text-foreground">Fees</h4>

      <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-5 gap-2">
        {metricRows.map((row) => (
          <div
            key={row.label}
            className="rounded-lg border border-border bg-muted/40 p-3"
          >
            <div className="text-xs text-muted-foreground">{row.label}</div>
            <div className="mt-1 break-all font-mono text-xs text-foreground">
              {row.value}
            </div>
          </div>
        ))}
      </div>

      {distributionRows.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-border">
          {distributionRows.map((row) => (
            <div
              key={row.label}
              className="grid grid-cols-[minmax(170px,220px)_1fr] border-b border-border last:border-b-0"
            >
              <div className="bg-muted/60 px-3 py-2 text-xs font-medium text-muted-foreground">
                {row.label}
              </div>
              <div className="break-all px-3 py-2 font-mono text-xs">
                {row.value}
              </div>
            </div>
          ))}
        </div>
      )}

      {recommendedRows.length > 0 && (
        <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
          <div className="rounded-lg border border-border p-3">
            <div className="mb-2 text-xs font-semibold text-foreground">
              Recommended Preset
            </div>
            {recommendedRows.map((row) => (
              <ReportRow key={row.label} label={row.label} value={row.value} />
            ))}
          </div>

          {observedRows.length > 0 && (
            <div className="rounded-lg border border-border p-3">
              <div className="mb-2 text-xs font-semibold text-foreground">
                Observed Usage
              </div>
              {observedRows.map((row) => (
                <ReportRow key={row.label} label={row.label} value={row.value} />
              ))}
            </div>
          )}
        </div>
      )}

      {report && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
          {report.proposalReceipt && (
            <div className="rounded-lg border border-border p-3">
              <div className="mb-2 text-xs font-semibold text-foreground">
                Proposal Receipt
              </div>
              <ReportRow
                label="Bytes"
                value={formatInteger(report.proposalReceipt.receiptBytes)}
              />
              <ReportRow
                label="Gas"
                value={formatInteger(report.proposalReceipt.estimatedGas)}
              />
              <ReportRow
                label="Fee"
                value={formatFeeAmount(report.proposalReceipt.fee)}
              />
            </div>
          )}

          {report.messageReveal && (
            <div className="rounded-lg border border-border p-3">
              <div className="mb-2 text-xs font-semibold text-foreground">
                Message Reveal
              </div>
              <ReportRow
                label="Messages"
                value={formatInteger(report.messageReveal.messageCount)}
              />
              <ReportRow
                label="Bytes"
                value={formatInteger(report.messageReveal.messageBytes)}
              />
              <ReportRow
                label="Gas"
                value={formatInteger(report.messageReveal.estimatedGas)}
              />
              <ReportRow
                label="Chain gas"
                value={formatInteger(report.messageReveal.consensusAdditionalGas)}
              />
              <ReportRow
                label="Fixed gas"
                value={formatInteger(report.messageReveal.studioFixedOverheadGas)}
              />
              <ReportRow
                label="Chain fee"
                value={formatFeeAmount(report.messageReveal.consensusAdditionalFee)}
              />
              <ReportRow
                label="Fixed fee"
                value={formatFeeAmount(report.messageReveal.studioFixedOverheadFee)}
              />
              <ReportRow
                label="Studio meter"
                value={formatFeeAmount(report.messageReveal.fee)}
              />
            </div>
          )}

          <div className="rounded-lg border border-border p-3">
            <div className="mb-2 text-xs font-semibold text-foreground">
              Execution Report
            </div>
            <ReportRow
              label="Receipt gas price"
              value={formatFeeAmount(report.receiptGasPrice)}
            />
            <ReportRow
              label="Estimated fee"
              value={formatFeeAmount(report.totalEstimatedFee)}
            />
            {report.totalStudioMeteredFee !== undefined && (
              <ReportRow
                label="Studio metered"
                value={formatFeeAmount(report.totalStudioMeteredFee)}
              />
            )}
            {report.budgetExhaustionReason && (
              <ReportRow
                label="Budget exhaustion"
                value={report.budgetExhaustionReason}
              />
            )}
            {messageFees && (
              <>
                <ReportRow
                  label="Message budget"
                  value={formatFeeAmount(messageFees.budget)}
                />
                <ReportRow
                  label="Declared message spent"
                  value={formatFeeAmount(messageFees.declaredConsumed)}
                />
                {messageFees.genvmMeteredConsumed !== undefined && (
                  <ReportRow
                    label="GenVM metered message"
                    value={formatFeeAmount(messageFees.genvmMeteredConsumed)}
                  />
                )}
                {messageFees.externalReserved !== undefined && (
                  <ReportRow
                    label="External reserved"
                    value={formatFeeAmount(messageFees.externalReserved)}
                  />
                )}
                {messageFees.externalReimbursed !== undefined && (
                  <ReportRow
                    label="External executor reimbursed"
                    value={formatFeeAmount(messageFees.externalReimbursed)}
                  />
                )}
                {messageFees.externalRemainder !== undefined && (
                  <ReportRow
                    label="External remainder"
                    value={formatFeeAmount(messageFees.externalRemainder)}
                  />
                )}
                {messageFees.totalConsumed !== undefined && (
                  <ReportRow
                    label="Total message spent"
                    value={formatFeeAmount(messageFees.totalConsumed)}
                  />
                )}
                {messageFees.reportedTotal !== undefined && (
                  <ReportRow
                    label="Reported message total"
                    value={formatFeeAmount(messageFees.reportedTotal)}
                  />
                )}
                <ReportRow
                  label="Declared message refunded"
                  value={formatFeeAmount(messageFees.declaredRefunded)}
                />
                <ReportRow
                  label="Message remaining"
                  value={formatFeeAmount(messageFees.remaining)}
                />
                <ReportRow
                  label="Metering delta"
                  value={formatFeeAmount(messageFees.meteringDelta)}
                />
              </>
            )}
            {executionMetering && (
              <>
                <ReportRow
                  label="Chargeable exec"
                  value={formatFeeAmount(executionMetering.chargeableExecutionFee)}
                />
                <ReportRow
                  label="GenVM raw exec"
                  value={formatFeeAmount(executionMetering.genvmReportedExecution)}
                />
                <ReportRow
                  label="Raw delta"
                  value={formatFeeAmount(executionMetering.genvmDeltaFromChargeable)}
                />
              </>
            )}
            {chargeableBucketRows.length > 0 && (
              <>
                <SectionLabel>Chargeable Buckets</SectionLabel>
                {chargeableBucketRows.map((row) => (
                  <ReportRow key={row.label} label={row.label} value={row.value} />
                ))}
              </>
            )}
            {genvmBucketRows.length > 0 && (
              <>
                <SectionLabel>GenVM Raw Buckets</SectionLabel>
                {genvmBucketRows.map((row) => (
                  <ReportRow key={row.label} label={row.label} value={row.value} />
                ))}
              </>
            )}
          </div>
        </div>
      )}

      {messages.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="min-w-full text-left text-xs">
            <thead className="border-b border-border bg-muted/60 text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium">Type</th>
                <th className="px-3 py-2 font-medium">Mode</th>
                <th className="px-3 py-2 font-medium">Recipient</th>
                <th className="px-3 py-2 font-medium">Value</th>
                <th className="px-3 py-2 font-medium">Data</th>
                <th className="px-3 py-2 font-medium">Fee Params</th>
                <th className="px-3 py-2 font-medium">Declared Budget</th>
                <th className="px-3 py-2 font-medium">Allocation</th>
                <th className="px-3 py-2 font-medium">On</th>
                <th className="px-3 py-2 font-medium">Call Key</th>
              </tr>
            </thead>
            <tbody>
              {messages.map((message, index) => (
                <tr
                  key={`${message.callKey}-${index}`}
                  className="border-b border-border last:border-b-0"
                >
                  <td className="px-3 py-2">{message.messageType}</td>
                  <td className="px-3 py-2">{message.messageFeeMode ?? '-'}</td>
                  <td className="px-3 py-2 font-mono">
                    {truncateAddress(message.recipient)}
                  </td>
                  <td className="px-3 py-2 font-mono">
                    {formatFeeAmount(message.value)}
                  </td>
                  <td className="px-3 py-2 font-mono">
                    {formatInteger(message.dataBytes)} B
                  </td>
                  <td className="px-3 py-2 font-mono">
                    {formatInteger(message.feeParamsBytes)} B
                    {message.feeParams && message.feeParams !== '0x' && (
                      <span className="block text-[10px] text-muted-foreground">
                        {truncateHash(message.feeParams)}
                      </span>
                    )}
                    {formatFeeParamsDecoded(message.feeParamsDecoded) !== '-' && (
                      <span className="block text-[10px] text-muted-foreground">
                        {formatFeeParamsDecoded(message.feeParamsDecoded)}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 font-mono">
                    {formatFeeAmount(message.declaredBudget)}
                  </td>
                  <td className="px-3 py-2 font-mono">
                    {formatInteger(message.allocationSubtreeBytes)} B
                    {message.allocationSubtree &&
                      message.allocationSubtree !== '0x' && (
                        <span className="block text-[10px] text-muted-foreground">
                          {truncateHash(message.allocationSubtree)}
                        </span>
                      )}
                  </td>
                  <td className="px-3 py-2">
                    {message.onAcceptance ? 'accepted' : 'finalized'}
                  </td>
                  <td className="px-3 py-2 font-mono">
                    {truncateHash(message.callKey)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ReportRow({
  label,
  value,
}: Readonly<{ label: string; value: string }>) {
  return (
    <div className="grid grid-cols-[120px_1fr] gap-2 py-1 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className="break-all font-mono text-foreground">{value}</span>
    </div>
  );
}

function SectionLabel({ children }: Readonly<{ children: string }>) {
  return (
    <div className="mt-3 border-b border-border pb-1 text-[11px] font-semibold uppercase text-muted-foreground first:mt-0">
      {children}
    </div>
  );
}
