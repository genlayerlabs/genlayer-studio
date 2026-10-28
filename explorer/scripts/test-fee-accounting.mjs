import assert from 'node:assert/strict';
import fs from 'node:fs';
import Module from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import ts from 'typescript';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const sourcePath = path.resolve(__dirname, '../src/lib/feeAccounting.ts');
const source = fs.readFileSync(sourcePath, 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    esModuleInterop: true,
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
  },
  fileName: sourcePath,
});

const testModule = new Module(sourcePath);
testModule.filename = sourcePath;
testModule.paths = Module._nodeModulePaths(path.dirname(sourcePath));
testModule._compile(compiled.outputText, sourcePath);

const {
  feeBucketRows,
  feeDistributionRows,
  feeMetricRows,
  feeRecommendedObservedRows,
  feeRecommendedPresetRows,
  formatFeeAmount,
  formatFeeParamsDecoded,
  formatInteger,
  getStudioFeeAccounting,
  toBigIntAmount,
} = testModule.exports;

function rowMap(rows) {
  return Object.fromEntries(rows.map((row) => [row.label, row.value]));
}

const WEI_PER_GEN = '1000000000000000000';
const accounting = {
  status: 'active',
  paid_fee_value: '120000000000000000',
  required_fee_value: '110000000000000000',
  primary_fee_budget: '100000000000000000',
  primary_fee_spent: '90000000000000000',
  primary_fee_refunded: '10000000000000000',
  execution_budget_total: '100000000000000000',
  execution_fee_consumed: '90000000000000000',
  genvm_message_fee_consumed: '1234',
  message_fee_budget: '55000000000000000',
  message_fee_consumed: '55000000000000000',
  message_fee_refunded: '0',
  external_message_fee_reserved: '700',
  external_message_fee_reimbursed: '420',
  external_message_fee_remainder: '280',
  appeal_bonds_total: '1400000000000000000',
  total_refunded: '10000000000000000',
  fees_distribution: {
    leaderTimeunitsAllocation: '100',
    validatorTimeunitsAllocation: '200',
    appealRounds: '1',
    executionBudgetPerRound: '50000000000000000',
    executionConsumed: '90000000000000000',
    totalMessageFees: '55000000000000000',
    rotations: ['0', '2'],
    maxPriceGenPerTimeUnit: '1000000000000000',
    storageFeeMaxGasPrice: '1',
    receiptFeeMaxGasPrice: '1',
  },
  recommended_fee_preset: {
    feeValue: '132000000000000000',
    paddingBps: '12000',
    numOfInitialValidators: '5',
    messageBudgetMode: 'allocation-preserved',
    messageAllocations: [{ messageType: 1, budget: '55000000000000000' }],
    distribution: {
      leaderTimeunitsAllocation: '120',
      validatorTimeunitsAllocation: '240',
      appealRounds: '2',
      executionBudgetPerRound: '60000000000000000',
      executionConsumed: '0',
      totalMessageFees: '55000000000000000',
      rotations: ['0', '1', '1'],
      maxPriceGenPerTimeUnit: '1000000000000000',
      storageFeeMaxGasPrice: '1',
      receiptFeeMaxGasPrice: '1',
    },
    observed: {
      executionFee: '90000000000000000',
      messageFeeBudget: '55000000000000000',
      declaredMessageFees: '55000000000000000',
      externalMessageReserved: '700',
      totalEstimatedFee: '145000000000000000',
      totalStudioMeteredFee: '145000000000000000',
    },
  },
};

assert.equal(toBigIntAmount('42'), 42n);
assert.equal(toBigIntAmount(42.9), 42n);
assert.equal(toBigIntAmount('not-a-number'), null);
assert.equal(formatInteger('1000000'), '1,000,000');
assert.equal(formatFeeAmount('999'), '999 wei');
assert.equal(
  formatFeeAmount('1000000000000000'),
  '0.001 GEN (1,000,000,000,000,000 wei)',
);
assert.equal(
  formatFeeAmount(WEI_PER_GEN),
  '1 GEN (1,000,000,000,000,000,000 wei)',
);
assert.equal(formatFeeParamsDecoded(null), '-');
assert.equal(formatFeeParamsDecoded({}), '-');
assert.equal(
  formatFeeParamsDecoded({
    leaderTimeunitsAllocation: 5,
    validatorTimeunitsAllocation: 10,
    appealRounds: 0,
    executionBudgetPerRound: 0,
    rotations: [0, 1],
  }),
  'Leader 5, Validator 10, Appeals 0, Exec budget 0 wei, Rotations 0 / 1',
);
assert.equal(
  formatFeeParamsDecoded({
    gasLimit: '21000',
    maxGasPrice: '1000000000000000',
  }),
  'Gas limit 21,000, Max gas price 0.001 GEN (1,000,000,000,000,000 wei)',
);
assert.equal(formatFeeParamsDecoded({ zeta: 'x', alpha: 3 }), 'alpha 3, zeta x');

assert.deepEqual(
  getStudioFeeAccounting({
    data: { fee_accounting: accounting },
    consensus_data: { fee_accounting: { status: 'ignored' } },
  }),
  accounting,
);
assert.deepEqual(
  getStudioFeeAccounting({
    data: {},
    consensus_data: { fee_accounting: accounting },
  }),
  accounting,
);
assert.deepEqual(
  getStudioFeeAccounting({
    data: {},
    consensus_data: {
      leader_receipt: [{ genvm_result: { fee_accounting: accounting } }],
    },
  }),
  accounting,
);
assert.equal(getStudioFeeAccounting({ data: {}, consensus_data: {} }), null);

const metrics = rowMap(feeMetricRows(accounting));
assert.equal(metrics['Paid fee'], '0.120 GEN (120,000,000,000,000,000 wei)');
assert.equal(metrics['Message budget'], '0.055 GEN (55,000,000,000,000,000 wei)');
assert.equal(metrics['GenVM message meter'], '1,234 wei');
assert.equal(metrics['External reimbursed'], '420 wei');
assert.equal(metrics['Appeal bonds'], '1.400 GEN (1,400,000,000,000,000,000 wei)');

const distribution = rowMap(feeDistributionRows(accounting));
assert.equal(distribution['Leader time units'], '100');
assert.equal(distribution.Rotations, '0 / 2');
assert.equal(
  distribution['Execution budget per round'],
  '0.050 GEN (50,000,000,000,000,000 wei)',
);
assert.equal(distribution['Max price per time unit'], '0.001 GEN (1,000,000,000,000,000 wei)');

const recommended = rowMap(feeRecommendedPresetRows(accounting));
assert.equal(recommended['Fee value'], '0.132 GEN (132,000,000,000,000,000 wei)');
assert.equal(recommended.Padding, '12,000 bps');
assert.equal(recommended.Validators, '5');
assert.equal(recommended['Message budget mode'], 'allocation-preserved');
assert.equal(recommended['Message allocations'], '1');

const observed = rowMap(feeRecommendedObservedRows(accounting));
assert.equal(observed['Execution fee'], '0.090 GEN (90,000,000,000,000,000 wei)');
assert.equal(observed['External reserved'], '700 wei');
assert.equal(observed['Studio metered fee'], '0.145 GEN (145,000,000,000,000,000 wei)');

const zeroBudgetBuckets = rowMap(
  feeBucketRows({
    receiptAndNondetOutput: '1',
    storage: '0',
    message: '0',
    totalExecution: '1',
    totalWithMessage: '1',
    executionBudgetPerRound: '0',
    executionBudgetRemaining: '0',
    executionBudgetOverrun: '1',
    executionBudgetExceeded: true,
  }),
);
assert.equal(zeroBudgetBuckets['Receipt/nondet used'], '1 wei');
assert.equal(zeroBudgetBuckets['Execution budget'], '0 wei');
assert.equal(zeroBudgetBuckets['Budget remaining'], '0 wei');
assert.equal(zeroBudgetBuckets['Budget overrun'], '1 wei');
assert.equal(zeroBudgetBuckets['Budget exceeded'], 'true');
assert.equal(zeroBudgetBuckets['Message meter'], '0 wei');

console.log('feeAccounting helper tests passed');
