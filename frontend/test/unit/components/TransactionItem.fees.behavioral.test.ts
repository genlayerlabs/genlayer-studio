import { mount } from '@vue/test-utils';
import { describe, expect, it, vi } from 'vitest';
import TransactionItem from '@/components/Simulator/TransactionItem.vue';

vi.mock('@/stores', () => ({
  useUIStore: vi.fn(() => ({ mode: 'light' })),
  useNodeStore: vi.fn(() => ({ searchFilter: '' })),
  useTransactionsStore: vi.fn(() => ({
    cancelTransaction: vi.fn(),
    setTransactionAppeal: vi.fn(),
  })),
}));

vi.mock('@kyvg/vue3-notification', () => ({
  notify: vi.fn(),
}));

vi.mock('@vueuse/core', () => ({
  useTimeAgo: vi.fn(() => ({ value: 'just now' })),
}));

vi.mock('@/utils/runtimeConfig', () => ({
  getRuntimeConfigBoolean: vi.fn(() => false),
  getRuntimeConfigNumber: vi.fn(() => 0.2),
}));

vi.mock('@/utils/explorerUrl', () => ({
  getExplorerUrl: vi.fn(() => 'http://explorer.local'),
}));

vi.mock('@/calldata/jsonifier', () => ({
  resultToUserFriendlyJson: vi.fn((value) => value),
  b64ToArray: vi.fn(() => []),
  calldataToUserFriendlyJson: vi.fn(() => ({})),
}));

const feeAccounting = {
  paid_fee_value: '132000000000000000',
  required_fee_value: '120000000000000000',
  primary_fee_budget: '100000000000000000',
  primary_fee_spent: '90000000000000000',
  primary_fee_refunded: '10000000000000000',
  execution_budget_total: '500000',
  execution_fee_consumed: '400000',
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
    executionBudgetPerRound: '500000',
    executionConsumed: '0',
    totalMessageFees: '55000000000000000',
    rotations: ['0', '1'],
    maxPriceGenPerTimeUnit: '1000000000000000',
    storageFeeMaxGasPrice: '1',
    receiptFeeMaxGasPrice: '1',
  },
  recommended_fee_preset: {
    feeValue: '132000000000000000',
    paddingBps: '12000',
    numOfInitialValidators: '5',
    messageBudgetMode: 'allocation-preserved',
    distribution: {
      leaderTimeunitsAllocation: '100',
      validatorTimeunitsAllocation: '200',
      appealRounds: '1',
      executionBudgetPerRound: '600000',
      executionConsumed: '0',
      totalMessageFees: '55000000000000000',
      rotations: ['0', '1'],
      maxPriceGenPerTimeUnit: '1200000000000000',
      storageFeeMaxGasPrice: '2',
      receiptFeeMaxGasPrice: '2',
    },
    observed: {
      executionFee: '400000',
      messageFeeBudget: '55000000000000000',
      declaredMessageFees: '55000000000000000',
      externalMessageReserved: '700',
      totalEstimatedFee: '600000',
      totalStudioMeteredFee: '600000',
    },
  },
  execution_fee_report: {
    receiptGasPrice: '1',
    proposalReceipt: {
      eqBlocksOutputsLength: '2',
      receiptBytes: '1026',
      estimatedGas: '314416',
      fee: '314416',
    },
    messageReveal: {
      messageCount: '1',
      messageBytes: '320',
      estimatedGas: '186120',
      fee: '186120',
      consensusAdditionalGas: '86120',
      consensusAdditionalFee: '86120',
      studioFixedOverheadGas: '100000',
      studioFixedOverheadFee: '100000',
      messages: [
        {
          messageType: 'Internal',
          messageFeeMode: 'mode2',
          recipient: '0x2222222222222222222222222222222222222222',
          value: '0',
          dataBytes: '2',
          onAcceptance: true,
          saltNonce: '0',
          feeParams: '0x1234567890abcdef',
          feeParamsDecoded: {
            leaderTimeunitsAllocation: 5,
            validatorTimeunitsAllocation: 10,
            appealRounds: 1,
            executionBudgetPerRound: '1000000000000000',
            rotations: [0, 1],
          },
          feeParamsBytes: '32',
          declaredBudget: '55000000000000000',
          allocationSubtree: '0xabcdef1234567890',
          allocationSubtreeBytes: '64',
          callKey:
            '0x7570646174655f73746f72616765000000000000000000000000000000000000',
        },
      ],
    },
    genvmBuckets: {
      receiptAndNondetOutput: '300000',
      storage: '100000',
      message: '1234',
      totalExecution: '400000',
      totalWithMessage: '401234',
      executionBudgetPerRound: '500000',
      executionBudgetRemaining: '100000',
      executionBudgetOverrun: '0',
      executionBudgetExceeded: false,
    },
    chargeableExecution: {
      receiptAndNondetOutput: '314416',
      storage: '0',
      message: '0',
      totalExecution: '400000',
      totalWithMessage: '400000',
      executionBudgetPerRound: '500000',
      executionBudgetRemaining: '100000',
      executionBudgetOverrun: '0',
      executionBudgetExceeded: false,
    },
    executionMetering: {
      chargeableExecutionFee: '400000',
      genvmReportedExecution: '401234',
      genvmDeltaFromChargeable: '1234',
    },
    messageFees: {
      budget: '55000000000000000',
      declaredConsumed: '55000000000000000',
      genvmMeteredConsumed: '1234',
      externalReserved: '700',
      externalReimbursed: '420',
      externalRemainder: '280',
      totalConsumed: '55000000000000000',
      declaredRefunded: '0',
      remaining: '0',
      meteringDelta: '1234',
      reportedTotal: '55000000000000000',
    },
    totalEstimatedFee: '600000',
    totalStudioMeteredFee: '600000',
  },
};

const transaction = {
  hash: '0xabcdef1234567890',
  type: 'method',
  statusName: 'ACCEPTED',
  decodedData: { functionName: 'update_storage' },
  data: {
    created_at: Date.now(),
    leader_only: false,
    appealed: false,
    appeal_failed: 0,
    appeal_processing_time: 0,
    timestamp_awaiting_finalization: Math.floor(Date.now() / 1000),
    data: {
      fee_accounting: feeAccounting,
    },
    consensus_data: {
      leader_receipt: [
        {
          execution_result: 'SUCCESS',
          result: { payload: { readable: '"ok"' } },
          genvm_result: {},
          eq_outputs: {},
        },
      ],
    },
  },
};

const ModalStub = {
  props: ['open'],
  template:
    '<section v-if="open"><slot name="title" /><slot name="info" /><slot /></section>',
};

describe('TransactionItem fee accounting display', () => {
  it('renders Studio fee reports and recommended presets in transaction details', async () => {
    const wrapper = mount(TransactionItem, {
      props: {
        transaction,
        finalityWindow: 60,
      },
      global: {
        directives: {
          tooltip: vi.fn(),
        },
        stubs: {
          Modal: ModalStub,
          Btn: {
            template: '<button @click="$emit(\'click\')"><slot /></button>',
          },
          CopyTextButton: true,
          JsonViewer: true,
          Loader: true,
          TransactionStatusBadge: { template: '<span><slot /></span>' },
          CheckCircleIcon: true,
          XCircleIcon: true,
          EllipsisHorizontalCircleIcon: true,
          FilterIcon: true,
          GavelIcon: true,
          UserPen: true,
          UserSearch: true,
          ExternalLink: true,
        },
      },
    });

    await wrapper.trigger('click');

    const text = wrapper.text();
    expect(text).toContain('Fees');
    expect(text).toContain('Recommended Preset');
    expect(text).toContain('Observed Usage');
    expect(text).toContain('Message Reveal');
    expect(text).toContain('Execution Report');
    expect(text).toContain('Message budget mode');
    expect(text).toContain('allocation-preserved');
    expect(text).toContain('Declared message spent');
    expect(text).toContain('External executor reimbursed');
    expect(text).toContain('Chargeable buckets');
    expect(text).toContain('GenVM raw buckets');
    expect(text).toContain('Receipt/nondet used');
    expect(text).toContain('Budget remaining');
    expect(text).toContain('Budget exceeded');
    expect(text).toContain('false');
    expect(text).toContain('mode2');
    expect(text).toContain(
      'Leader 5, Validator 10, Appeals 1, Exec budget 0.001 GEN (1,000,000,000,000,000 wei), Rotations 0 / 1',
    );
    expect(text).toContain('55,000,000,000,000,000 wei');
  });
});
