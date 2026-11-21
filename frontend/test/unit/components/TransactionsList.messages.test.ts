import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { reactive } from 'vue';
import { abi } from 'genlayer-js';
import TransactionItemComponent from '@/components/Simulator/TransactionItem.vue';
import TransactionsList from '@/components/Simulator/TransactionsList.vue';

const getSentTransactions = vi.fn();
const getTransaction = vi.fn();
const getTransactionStatus = vi.fn();
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
  useRpcClient: () => ({ getSentTransactions, getTransactionStatus }),
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
  getTransactionStatus.mockReset();
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

  // Server-like paging over a mutable history, newest first, keyed by a
  // (created_at, hash) cursor, limit capped at 100.
  let history: { hash: string; at: number; status?: string }[] = [];
  const rowAt = (n: number) => ({
    hash: `0x${n.toString(16).padStart(64, '0')}`,
    at: Date.UTC(2026, 10, 1) + n * 60_000,
  });
  const serve = (total: number) => {
    history = Array.from({ length: total }, (_, n) => rowAt(n + 1));
    getSentTransactions.mockImplementation(async ({ limit, before }) => {
      const sorted = [...history].sort((x, y) =>
        x.at !== y.at ? y.at - x.at : y.hash.localeCompare(x.hash),
      );
      const start = before
        ? sorted.findIndex((r) => r.hash === before.hash) + 1
        : 0;
      return sorted
        .slice(start, start + Math.min(limit, 100))
        .map((r) =>
          rpcRow(
            r.hash,
            addrA,
            new Date(r.at).toISOString().replace('Z', ''),
            r.status ? { status: r.status } : {},
          ),
        );
    });
  };
  const insertMessages = (count: number) => {
    const max = Math.max(...history.map((r) => r.at));
    for (let i = 1; i <= count; i++) {
      history.push({
        ...rowAt(0),
        hash: `0xffff${(history.length + i).toString(16).padStart(60, '0')}`,
        at: max + i * 60_000,
      });
    }
  };
  const poll = async () => {
    await vi.advanceTimersByTimeAsync(5_000);
    await flushPromises();
  };
  const loadAll = async (wrapper: any) => {
    for (let i = 0; i < 20; i++) {
      const more = wrapper.find('[data-testid="load-more-messages"]');
      if (!more.exists()) break;
      await more.trigger('click');
      await flushPromises();
    }
  };

  it('loads older pages by cursor beyond 100 rows', async () => {
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
    const cursors = getSentTransactions.mock.calls.map((c) => c[0].before);
    expect(cursors.filter(Boolean)).toHaveLength(6);
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

  it('keeps history contiguous when messages arrive before loading older pages', async () => {
    vi.useFakeTimers();
    serve(40);
    const wrapper = mountList();
    await flushPromises();
    expect(wrapper.findAll('.group')).toHaveLength(20);

    insertMessages(1);
    await poll();
    await loadAll(wrapper);
    expect(wrapper.findAll('.group')).toHaveLength(41);
    wrapper.unmount();
  });

  it('keeps history contiguous when messages arrive after loading older pages', async () => {
    vi.useFakeTimers();
    serve(40);
    const wrapper = mountList();
    await flushPromises();
    await loadAll(wrapper);
    expect(wrapper.findAll('.group')).toHaveLength(40);

    insertMessages(1);
    await poll();
    expect(wrapper.findAll('.group')).toHaveLength(41);
    insertMessages(3);
    await poll();
    expect(wrapper.findAll('.group')).toHaveLength(44);
    wrapper.unmount();
  });

  it('restarts from the head instead of leaving a gap after a burst of messages', async () => {
    vi.useFakeTimers();
    serve(40);
    const wrapper = mountList();
    await flushPromises();
    await loadAll(wrapper);

    insertMessages(30); // more than a page between two polls
    await poll();
    await loadAll(wrapper);
    expect(wrapper.findAll('.group')).toHaveLength(70);
    wrapper.unmount();
  });

  it('renders without crashing for a contract that is not deployed yet', async () => {
    contracts.currentContractId = 'undeployed';
    const wrapper = mountList();
    await flushPromises();
    expect(getSentTransactions).not.toHaveBeenCalled();
    expect(wrapper.find('[data-testid="load-more-messages"]').exists()).toBe(
      false,
    );
    expect(wrapper.find('.empty').exists()).toBe(true);
    wrapper.unmount();
  });

  const statuses = (wrapper: any) =>
    wrapper
      .findAllComponents(TransactionItemComponent)
      .map((c: any) => c.props('transaction').statusName);

  it('refreshes the status of older loaded rows that are not final', async () => {
    vi.useFakeTimers();
    serve(40);
    history[0].status = 'PENDING'; // oldest row, on the second page
    const wrapper = mountList();
    await flushPromises();
    await loadAll(wrapper);
    expect(statuses(wrapper)[39]).toBe('PENDING');

    getTransactionStatus.mockResolvedValue('FINALIZED');
    await poll();
    expect(getTransactionStatus).toHaveBeenCalledTimes(1);
    expect(getTransaction).not.toHaveBeenCalled(); // no full details
    expect(statuses(wrapper)[39]).toBe('FINALIZED');
    wrapper.unmount();
  });

  it('rotates through more nonterminal older rows than the per-poll budget', async () => {
    vi.useFakeTimers();
    serve(40);
    for (let i = 0; i < 11; i++) history[i].status = 'PENDING'; // 11 oldest
    const wrapper = mountList();
    await flushPromises();
    await loadAll(wrapper);

    const finalized = new Set<string>();
    getTransactionStatus.mockImplementation(async ({ hash }) => {
      finalized.add(hash);
      return 'FINALIZED';
    });
    await poll();
    expect(getTransactionStatus).toHaveBeenCalledTimes(10);
    await poll();
    // The eleventh row is reached on the next poll, not starved.
    expect(finalized.size).toBe(11);
    expect(
      statuses(wrapper).filter((x: string) => x === 'PENDING'),
    ).toHaveLength(0);
    expect(getTransaction).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it('applies the status of the details fetched on open to the row', async () => {
    vi.useFakeTimers();
    serve(40);
    history[0].status = 'PENDING';
    const wrapper = mountList();
    await flushPromises();
    await loadAll(wrapper);

    getTransaction.mockResolvedValue({
      statusName: 'FINALIZED',
      hash: history[0].hash,
    });
    await wrapper.findAll('.group')[39].trigger('click');
    await flushPromises();
    expect(statuses(wrapper)[39]).toBe('FINALIZED');
    wrapper.unmount();
  });

  it('clear keeps a message that is newer by less than a millisecond', async () => {
    const hash = (n: number) => `0x${n.toString(16).padStart(64, '0')}`;
    const rows = [
      { hash: hash(2), created_at: '2026-11-18T10:00:00.123900' },
      { hash: hash(1), created_at: '2026-11-18T10:00:00.123100' },
    ];
    let visible = rows.slice(1); // only the older one exists at clear time
    getSentTransactions.mockImplementation(async () =>
      visible.map((r) => rpcRow(r.hash, addrA, r.created_at)),
    );
    vi.useFakeTimers();
    const wrapper = mountList();
    await flushPromises();
    await wrapper.find('.clear').trigger('click');
    await wrapper.find('.ok').trigger('click');
    await flushPromises();
    expect(wrapper.findAll('.group')).toHaveLength(0);

    visible = rows; // a message within the same millisecond arrives
    await poll();
    expect(wrapper.findAll('.group')).toHaveLength(1);
    wrapper.unmount();
  });
});
