import { flushPromises, mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import TransactionItem from '@/components/Simulator/TransactionItem.vue';
import { notify } from '@kyvg/vue3-notification';

const getTransactionMock = vi.fn();

vi.mock('@/stores', () => ({
  useUIStore: vi.fn(() => ({ mode: 'light' })),
  useNodeStore: vi.fn(() => ({ searchFilter: '' })),
  useTransactionsStore: vi.fn(() => ({
    cancelTransaction: vi.fn(),
    setTransactionAppeal: vi.fn(),
    getTransaction: getTransactionMock,
  })),
}));

vi.mock('@kyvg/vue3-notification', () => ({
  notify: vi.fn(),
}));

vi.mock('@vueuse/core', () => ({
  useTimeAgo: vi.fn(() => ({ value: 'just now' })),
  useTimestamp: vi.fn(() => ({ value: Date.now() })),
}));

vi.mock('@/utils/runtimeConfig', () => ({
  getRuntimeConfigBoolean: vi.fn(() => false),
  getRuntimeConfigNumber: vi.fn(() => 0),
}));

vi.mock('@/utils/explorerUrl', () => ({
  getExplorerUrl: vi.fn(() => 'http://explorer.local'),
}));

vi.mock('@/calldata/jsonifier', () => ({
  resultToUserFriendlyJson: vi.fn((value) => value),
  b64ToArray: vi.fn(() => []),
  calldataToUserFriendlyJson: vi.fn(() => ({})),
}));

const parentHash = `0x${'a'.repeat(64)}` as const;
const childHash = `0x${'b'.repeat(64)}` as const;
const grandchildHash = `0x${'c'.repeat(64)}` as const;

const parentTransaction = {
  hash: parentHash,
  type: 'method' as const,
  statusName: 'FINALIZED' as const,
  contractAddress: '0x1111111111111111111111111111111111111111',
  localContractId: 'contract-1',
  data: {
    hash: parentHash,
    created_at: '2026-11-18T10:00:00Z',
    statusName: 'FINALIZED',
    triggered_transactions: [childHash],
  },
};

const ModalStub = {
  props: ['open'],
  emits: ['close'],
  template:
    '<section v-if="open" class="modal"><button class="modal-close" @click="$emit(\'close\')" /><slot name="title" /><slot name="info" /><slot /></section>',
};

function mountItem(transaction: any = parentTransaction) {
  return mount(TransactionItem, {
    props: { transaction, finalityWindow: 60 },
    global: {
      directives: { tooltip: vi.fn() },
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
}

describe('TransactionItem messages (triggered transactions)', () => {
  beforeEach(() => {
    getTransactionMock.mockReset();
    vi.mocked(notify).mockReset();
  });

  it('lists triggered transactions as links in the details modal', async () => {
    const wrapper = mountItem();
    await wrapper.trigger('click');

    expect(wrapper.text()).toContain('Messages');
    const link = wrapper.find(
      `[data-testid="triggered-transaction-link-${childHash}"]`,
    );
    expect(link.exists()).toBe(true);
    expect(link.text()).toBe(childHash);
  });

  it('hides the messages section when nothing was triggered', async () => {
    const wrapper = mountItem({
      ...parentTransaction,
      data: { ...parentTransaction.data, triggered_transactions: [] },
    });
    await wrapper.trigger('click');

    expect(
      wrapper.find('[data-testid="triggered-transactions"]').exists(),
    ).toBe(false);
  });

  it('opens a nested modal with the triggered transaction details', async () => {
    getTransactionMock.mockResolvedValue({
      hash: childHash,
      type: 2,
      statusName: 'ACCEPTED',
      to_address: '0x2222222222222222222222222222222222222222',
      created_at: '2026-11-18T10:00:01Z',
      triggered_by: parentHash,
      triggered_transactions: [grandchildHash],
    });

    const wrapper = mountItem();
    await wrapper.trigger('click');
    await wrapper
      .find(`[data-testid="triggered-transaction-link-${childHash}"]`)
      .trigger('click');
    await flushPromises();

    expect(getTransactionMock).toHaveBeenCalledWith(childHash);
    const items = wrapper.findAllComponents(TransactionItem);
    // Only the nested child details (the root wrapper is not included).
    expect(items).toHaveLength(1);
    const child = items[0]!;
    expect(child.props('detailsOnly')).toBe(true);
    expect(child.props('transaction')).toMatchObject({
      hash: childHash,
      type: 'method',
      statusName: 'ACCEPTED',
      contractAddress: '0x2222222222222222222222222222222222222222',
    });
    expect(child.text()).toContain(childHash);
    // The child modal lists its own messages, so they can be followed further.
    expect(
      child
        .find(`[data-testid="triggered-transaction-link-${grandchildHash}"]`)
        .exists(),
    ).toBe(true);

    // Closing the nested modal removes it and leaves the parent open.
    const closeButtons = child.findAll('.modal-close');
    await closeButtons[0]!.trigger('click');
    expect(wrapper.findAllComponents(TransactionItem)).toHaveLength(0);
    expect(wrapper.text()).toContain('Messages');
  });

  it('notifies when the triggered transaction cannot be loaded', async () => {
    getTransactionMock.mockRejectedValue(new Error('boom'));

    const wrapper = mountItem();
    await wrapper.trigger('click');
    await wrapper
      .find(`[data-testid="triggered-transaction-link-${childHash}"]`)
      .trigger('click');
    await flushPromises();

    expect(notify).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'error', text: 'boom' }),
    );
    expect(wrapper.findAllComponents(TransactionItem)).toHaveLength(0);
  });
});
