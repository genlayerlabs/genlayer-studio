import { flushPromises, mount } from '@vue/test-utils';
import { describe, expect, it, vi } from 'vitest';
import type { ContractMethod } from 'genlayer-js/types';
import ContractMethodItem from '@/components/Simulator/ContractMethodItem.vue';

const mocks = vi.hoisted(() => ({
  callWriteMethod: vi.fn(),
  callReadMethod: vi.fn(),
  simulateWriteMethod: vi.fn(),
  estimateWriteMethodFees: vi.fn(),
  trackEvent: vi.fn(),
  notify: vi.fn(),
}));

vi.mock('@/hooks', () => ({
  useContractQueries: vi.fn(() => ({
    callWriteMethod: mocks.callWriteMethod,
    callReadMethod: mocks.callReadMethod,
    simulateWriteMethod: mocks.simulateWriteMethod,
    estimateWriteMethodFees: mocks.estimateWriteMethodFees,
    contract: { value: { name: 'Storage' } },
  })),
  useEventTracking: vi.fn(() => ({
    trackEvent: mocks.trackEvent,
  })),
}));

vi.mock('@kyvg/vue3-notification', () => ({
  notify: mocks.notify,
}));

vi.mock('vue-collapsed', () => ({
  Collapse: {
    props: ['when'],
    template: '<div v-if="when"><slot /></div>',
  },
}));

vi.mock('genlayer-js', () => ({
  abi: {
    calldata: {
      toString: vi.fn((value) => String(value)),
    },
  },
}));

vi.mock('genlayer-js/types', () => ({
  TransactionHashVariant: {
    LATEST_FINAL: 'latest-final',
    LATEST_NONFINAL: 'latest-nonfinal',
  },
}));

describe('ContractMethodItem fee estimation', () => {
  it('renders a structured Studio fee estimate summary and preserves raw JSON', async () => {
    mocks.estimateWriteMethodFees.mockResolvedValueOnce({
      scenario: 'update_storage',
      feeReport: {
        totalEstimatedFee: '600000',
        proposalReceipt: {
          receiptBytes: '256',
          estimatedGas: '512',
          fee: '512',
        },
        messageReveal: {
          messageCount: '1',
          messageBytes: '64',
          estimatedGas: '128',
          fee: '128',
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
        chargeableExecution: {
          storage: '10000',
          receiptAndNondetOutput: '20000',
          message: '30000',
          totalExecution: '400000',
        },
        executionMetering: {
          chargeableExecutionFee: '400000',
          genvmReportedExecution: '401234',
        },
        messageFees: {
          declaredConsumed: '55000000000000000',
          externalReserved: '700',
          externalReimbursed: '400',
          externalRemainder: '300',
          remaining: '0',
        },
      },
      recommendedPreset: {
        feeValue: '132000000000000000',
        paddingBps: '12000',
        messageBudgetMode: 'allocation-preserved',
        distribution: {
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
        observed: {
          executionFee: '400000',
          messageFeeBudget: '55000000000000000',
          declaredMessageFees: '55000000000000000',
          externalMessageReserved: '700',
          totalEstimatedFee: '600000',
          totalStudioMeteredFee: '600000',
        },
      },
    });

    const wrapper = mount(ContractMethodItem, {
      props: {
        name: 'update_storage',
        method: {
          name: 'update_storage',
          args: [],
          kwargs: {},
          payable: false,
        } as ContractMethod,
        methodType: 'write',
        executionMode: 'NORMAL',
        simulationMode: true,
      },
      global: {
        stubs: {
          Btn: {
            props: ['loading', 'disabled'],
            emits: ['click'],
            template:
              '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
          },
          ContractParams: {
            template: '<div />',
          },
          ChevronDownIcon: true,
        },
      },
    });

    await wrapper
      .find('[data-testid="expand-method-btn-update_storage"]')
      .trigger('click');
    await wrapper
      .find('[data-testid="estimate-fees-btn-update_storage"]')
      .trigger('click');
    await flushPromises();
    await wrapper.vm.$nextTick();

    expect(mocks.estimateWriteMethodFees).toHaveBeenCalledWith({
      method: 'update_storage',
      args: { args: [], kwargs: {} },
      value: undefined,
    });

    const summary = wrapper.find(
      '[data-testid="fee-estimate-summary-update_storage"]',
    );
    expect(summary.exists()).toBe(true);
    const text = summary.text();
    expect(text).toContain('Scenario');
    expect(text).toContain('update_storage');
    expect(text).toContain('Recommended fee value');
    expect(text).toContain('132,000,000,000,000,000 wei');
    expect(text).toContain('Execution budget / round');
    expect(text).toContain('500,000 wei');
    expect(text).toContain('Leader time units');
    expect(text).toContain('100');
    expect(text).toContain('Validator time units');
    expect(text).toContain('200');
    expect(text).toContain('Message fee budget');
    expect(text).toContain('55,000,000,000,000,000 wei');
    expect(text).toContain('Appeal rounds');
    expect(text).toContain('Rotations');
    expect(text).toContain('0, 1');
    expect(text).toContain('Proposal receipt bytes');
    expect(text).toContain('256');
    expect(text).toContain('Proposal receipt gas');
    expect(text).toContain('512');
    expect(text).toContain('Message count');
    expect(text).toContain('1');
    expect(text).toContain('Message bytes');
    expect(text).toContain('64');
    expect(text).toContain('Message reveal gas');
    expect(text).toContain('128');
    expect(text).toContain('Message budget mode');
    expect(text).toContain('allocation-preserved');
    expect(text).toContain('Total estimated fee');
    expect(text).toContain('600,000 wei');
    expect(text).toContain('Chargeable execution');
    expect(text).toContain('400,000 wei');
    expect(text).toContain('Chargeable storage');
    expect(text).toContain('10,000 wei');
    expect(text).toContain('Chargeable receipt/non-det');
    expect(text).toContain('20,000 wei');
    expect(text).toContain('Chargeable message');
    expect(text).toContain('30,000 wei');
    expect(text).toContain('GenVM raw execution');
    expect(text).toContain('401,234 wei');
    expect(text).toContain('Observed external reserve');
    expect(text).toContain('700 wei');
    expect(text).toContain('External message reserved');
    expect(text).toContain('External message reimbursed');
    expect(text).toContain('400 wei');
    expect(text).toContain('External message remainder');
    expect(text).toContain('300 wei');
    expect(text).toContain('Message fees remaining');
    expect(text).toContain('0 wei');

    const messages = wrapper.find(
      '[data-testid="fee-estimate-messages-update_storage"]',
    );
    expect(messages.exists()).toBe(true);
    const messagesText = messages.text();
    expect(messagesText).toContain('Internal');
    expect(messagesText).toContain('mode2');
    expect(messagesText).toContain('32 B');
    expect(messagesText).toContain(
      'Leader 5, Validator 10, Appeals 1, Exec budget 1,000,000,000,000,000 wei, Rotations 0 / 1',
    );
    expect(messagesText).toContain('55,000,000,000,000,000 wei');
    expect(messagesText).toContain('64 B');
    expect(messagesText).toContain('accepted');

    expect(
      wrapper
        .find('[data-testid="fee-estimate-response-update_storage"]')
        .text(),
    ).toContain('"recommendedPreset"');
  });
});
