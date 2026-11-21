import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { reactive } from 'vue';
import { abi } from 'genlayer-js';
import TransactionsList from '@/components/Simulator/TransactionsList.vue';

const getSentTransactions = vi.fn();
const getTransaction = vi.fn();
const clearTransactionsForContract = vi.fn();

const addrA = '0xAbCd000000000000000000000000000000000001';
const addrB = '0xAbCd000000000000000000000000000000000002';

const contracts = reactive({
  currentContractId: 'a',
  deployedContracts: [
    { contractId: 'a', address: addrA },
    { contractId: 'b', address: addrB },
  ],
});
const network = reactive({ isStudio: true });
const txs = reactive({
  transactions: [] as any[],
  clearTransactionsForContract,
  getTransaction,
});

vi.mock('@/stores', () => ({
  useContractsStore: () => contracts,
  useNetworkStore: () => network,
  useTransactionsStore: () => txs,
  useUIStore: () => ({ mode: 'light' }),
  useNodeStore: () => ({ searchFilter: '' }),
}));
vi.mock('@/hooks', () => ({
  useRpcClient: () => ({ getSentTransactions }),
}));
vi.mock('@kyvg/vue3-notification', () => ({ notify: vi.fn() }));
vi.mock('@vueuse/core', () => ({
  useTimeAgo: () => ({ value: 'just now' }),
  useTimestamp: () => ({ value: Date.now() }),
}));
vi.mock('@/utils/runtimeConfig', () => ({
  getRuntimeConfigBoolean: () => false,
  getRuntimeConfigNumber: () => 0,
}));
vi.mock('@/utils/explorerUrl', () => ({ getExplorerUrl: () => 'http://x' }));

const calldata = (method: string, args: any[]) =>
  btoa(
    String.fromCharCode(
      ...abi.calldata.encode(abi.calldata.makeCalldataObject(method, args, {})),
    ),
  );

// What sim_getSentTransactions returns for a row.
const rpcRow = (hash: string, from: string, createdAt: string, extra = {}) => ({
  hash,
  from_address: from,
  to_address: '0x9999999999999999999999999999999999999999',
  type: 2,
  status: 'FINALIZED',
  created_at: createdAt,
  triggered_by: '0xparent',
  data: { calldata: calldata('ping', [7]) },
  ...extra,
});

const ModalStub = {
  props: ['open'],
  template:
    '<section v-if="open" class="modal"><slot name="title" /><slot /></section>',
};

const mountList = () =>
  mount(TransactionsList, {
    global: {
      directives: { tooltip: {} },
      stubs: {
        PageSection: {
          template: '<div><slot name="actions" /><slot /></div>',
        },
        GhostBtn: {
          template: '<button class="clear" @click="$emit(\'click\')" />',
        },
        ConfirmationModal: {
          props: ['open'],
          emits: ['confirm'],
          template:
            '<div v-if="open" class="confirm"><button class="ok" @click="$emit(\'confirm\')" /></div>',
        },
        EmptyListPlaceholder: { template: '<p class="empty"><slot /></p>' },
        Modal: ModalStub,
        Btn: true,
        CopyTextButton: true,
        JsonViewer: true,
        Loader: true,
        TransactionStatusBadge: true,
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

const rowHashes = (w: any) => w.findAll('.font-mono').map((x: any) => x.text());

beforeEach(() => {
  localStorage.clear();
  getSentTransactions.mockReset();
  getTransaction.mockReset();
  contracts.currentContractId = 'a';
  network.isStudio = true;
  txs.transactions = [];
});
afterEach(() => {
  vi.resetAllMocks();
  vi.useRealTimers();
});

describe('TransactionsList messages', () => {
  it('renders sent rows with method name and decoded Input from a real RPC row', async () => {
    getSentTransactions.mockResolvedValue([
      rpcRow(`0x${'1'.repeat(64)}`, addrA, '2026-11-18T11:00:00Z'),
    ]);
    const wrapper = mountList();
    await flushPromises();

    expect(getSentTransactions).toHaveBeenCalledWith({
      address: addrA.toLowerCase(),
      limit: 20,
    });
    expect(wrapper.text()).toContain('ping');

    getTransaction.mockResolvedValue({ hash: `0x${'1'.repeat(64)}` });
    await wrapper.find('.group').trigger('click');
    await flushPromises();
    expect(wrapper.find('.modal').exists()).toBe(true);
    expect(wrapper.find('.modal').text()).toContain('Input');
    expect(wrapper.find('.modal pre').text()).toContain('7');
    expect(getTransaction).toHaveBeenCalledTimes(1); // details on demand
    wrapper.unmount();
  });

  it('does not query outside Studio', async () => {
    network.isStudio = false;
    const wrapper = mountList();
    await flushPromises();
    expect(getSentTransactions).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it('drops the previous contract rows on switch, even while pending or failing', async () => {
    getSentTransactions.mockResolvedValueOnce([
      rpcRow(`0x${'a'.repeat(64)}`, addrA, '2026-11-18T11:00:00Z'),
    ]);
    const wrapper = mountList();
    await flushPromises();
    expect(wrapper.findAll('.group')).toHaveLength(1);

    let rejectB: (e: Error) => void = () => {};
    getSentTransactions.mockReturnValueOnce(
      new Promise((_, reject) => (rejectB = reject)),
    );
    contracts.currentContractId = 'b';
    await flushPromises();
    expect(wrapper.findAll('.group')).toHaveLength(0); // pending

    rejectB(new Error('boom'));
    await flushPromises();
    expect(wrapper.findAll('.group')).toHaveLength(0); // failed
    wrapper.unmount();
  });

  it('keeps one request in flight and accepts responses slower than the poll interval', async () => {
    vi.useFakeTimers();
    const resolvers: Array<(v: any[]) => void> = [];
    getSentTransactions.mockImplementation(
      () => new Promise((resolve) => resolvers.push(resolve)),
    );
    const wrapper = mountList();
    await flushPromises();

    await vi.advanceTimersByTimeAsync(20_000); // way past the interval
    expect(getSentTransactions).toHaveBeenCalledTimes(1);

    resolvers[0]([
      rpcRow(`0x${'c'.repeat(64)}`, addrA, '2026-11-18T11:00:00Z'),
    ]);
    await flushPromises();
    expect(wrapper.findAll('.group')).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(5_000);
    expect(getSentTransactions).toHaveBeenCalledTimes(2);
    wrapper.unmount();
  });

  it('clear hides fetched messages too', async () => {
    getSentTransactions.mockResolvedValue([
      rpcRow(`0x${'d'.repeat(64)}`, addrA, '2026-11-18T11:00:00Z'),
    ]);
    const wrapper = mountList();
    await flushPromises();
    expect(wrapper.findAll('.group')).toHaveLength(1);

    await wrapper.find('.clear').trigger('click');
    await wrapper.find('.ok').trigger('click');
    await flushPromises();
    expect(clearTransactionsForContract).toHaveBeenCalledWith('a');
    expect(wrapper.findAll('.group')).toHaveLength(0);
    expect(wrapper.find('.empty').exists()).toBe(true);
    wrapper.unmount();
  });

  // Server-like paging over `total` rows, newest first, limit capped at 100.
  const serve = (total: number) =>
    getSentTransactions.mockImplementation(async ({ limit, offset = 0 }) =>
      Array.from(
        { length: Math.max(0, Math.min(total - offset, Math.min(limit, 100))) },
        (_, i) => {
          const n = offset + i;
          return rpcRow(
            `0x${n.toString(16).padStart(64, '0')}`,
            addrA,
            new Date(
              Date.UTC(2026, 10, 1) + (total - n) * 60_000,
            ).toISOString(),
          );
        },
      ),
    );

  it('loads older pages by offset beyond 100 rows', async () => {
    serve(130);
    const wrapper = mountList();
    await flushPromises();
    expect(wrapper.findAll('.group')).toHaveLength(20);

    for (let i = 0; i < 10; i++) {
      const more = wrapper.find('[data-testid="load-more-messages"]');
      if (!more.exists()) break;
      await more.trigger('click');
      await flushPromises();
    }
    expect(wrapper.findAll('.group')).toHaveLength(130);
    expect(wrapper.find('[data-testid="load-more-messages"]').exists()).toBe(
      false,
    );
    const offsets = getSentTransactions.mock.calls.map((c) => c[0].offset);
    expect(offsets).toContain(100);
    expect(getSentTransactions.mock.calls.every((c) => c[0].limit <= 20)).toBe(
      true,
    );
    wrapper.unmount();
  });

  it('clear covers older pages and survives switching contracts', async () => {
    serve(50);
    const wrapper = mountList();
    await flushPromises();
    await wrapper.find('.clear').trigger('click');
    await wrapper.find('.ok').trigger('click');
    await flushPromises();
    expect(wrapper.findAll('.group')).toHaveLength(0);

    // Older messages stay hidden after Load more...
    await wrapper.find('[data-testid="load-more-messages"]').trigger('click');
    await flushPromises();
    expect(wrapper.findAll('.group')).toHaveLength(0);

    // ...and after switching away and back.
    contracts.currentContractId = 'b';
    await flushPromises();
    contracts.currentContractId = 'a';
    await flushPromises();
    expect(wrapper.findAll('.group')).toHaveLength(0);
    wrapper.unmount();
  });
});
