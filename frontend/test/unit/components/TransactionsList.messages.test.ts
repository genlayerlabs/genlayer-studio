import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ref } from 'vue';
import TransactionsList from '@/components/Simulator/TransactionsList.vue';

const getTransactionsForAddress = vi.fn();
const address = '0xAbCd000000000000000000000000000000000001';
const isStudio = ref(true);

vi.mock('@/stores', () => ({
  useContractsStore: vi.fn(() => ({
    currentContractId: 'c1',
    deployedContracts: [{ contractId: 'c1', address }],
  })),
  useNetworkStore: vi.fn(() => ({
    get isStudio() {
      return isStudio.value;
    },
  })),
  useTransactionsStore: vi.fn(() => ({
    transactions: [
      {
        hash: '0xincoming',
        localContractId: 'c1',
        data: { created_at: '2026-11-18T10:00:00Z' },
      },
    ],
    clearTransactionsForContract: vi.fn(),
  })),
}));

vi.mock('@/hooks', () => ({
  useRpcClient: vi.fn(() => ({ getTransactionsForAddress })),
}));

const mountList = () =>
  mount(TransactionsList, {
    global: {
      directives: { tooltip: {} },
      stubs: {
        PageSection: { template: '<div><slot /></div>' },
        GhostBtn: true,
        ConfirmationModal: true,
        EmptyListPlaceholder: true,
        TransactionItem: {
          props: ['transaction'],
          template: '<div class="tx">{{ transaction.hash }}</div>',
        },
      },
    },
  });

afterEach(() => {
  vi.clearAllMocks();
  isStudio.value = true;
});

describe('TransactionsList messages', () => {
  it('shows transactions sent by the contract next to received ones', async () => {
    getTransactionsForAddress.mockResolvedValue([
      {
        hash: '0xincoming',
        from_address: '0xuser',
        to_address: address,
        created_at: '2026-11-18T10:00:00Z',
      },
      {
        hash: '0xmessage',
        from_address: address.toLowerCase(),
        to_address: '0xother',
        created_at: '2026-11-18T11:00:00Z',
      },
      { hash: '0xunrelated', from_address: '0xuser', to_address: '0xother' },
    ]);
    const wrapper = mountList();
    await flushPromises();
    expect(getTransactionsForAddress).toHaveBeenCalledWith({
      address: address.toLowerCase(),
    });
    expect(wrapper.findAll('.tx').map((w) => w.text())).toEqual([
      '0xmessage',
      '0xincoming',
    ]);
    wrapper.unmount();
  });

  it('does not query messages outside Studio', async () => {
    isStudio.value = false;
    const wrapper = mountList();
    await flushPromises();
    expect(getTransactionsForAddress).not.toHaveBeenCalled();
    expect(wrapper.findAll('.tx')).toHaveLength(1);
    wrapper.unmount();
  });
});
