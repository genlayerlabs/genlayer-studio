import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import TransactionItem from '@/components/Simulator/TransactionItem.vue';
import { notify } from '@kyvg/vue3-notification';
import { createClient } from 'genlayer-js';
import { localnet } from 'genlayer-js/chains';

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

// The real Modal is teleported (headless UI Dialog), so clicks inside it never
// bubble to the list row; .stop mirrors that for the inline stub.
const ModalStub = {
  props: ['open'],
  emits: ['close'],
  template:
    '<section v-if="open" class="modal"><button class="modal-close" @click.stop="$emit(\'close\')" /><slot name="title" /><slot name="info" /><slot /></section>',
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

  it('shows the SDK response with one leader receipt and Studio validation data', async () => {
    const validationReceipt = {
      vote: 'agree',
      result: 'AXg=',
      calldata: 'AA==',
      eq_outputs: { validator: 'AXg=' },
    };
    vi.stubGlobal('fetch', async (_url: string, options: { body: string }) => {
      const { id, method } = JSON.parse(options.body);
      expect(method).toBe('eth_getTransactionByHash');
      return Response.json({
        jsonrpc: '2.0',
        id,
        result: {
          hash: parentHash,
          status: 'FINALIZED',
          data: {},
          consensus_data: {
            leader_receipt: [{ execution_result: 'SUCCESS' }],
          },
          studio_data: { leader_validation_receipt: validationReceipt },
        },
      });
    });
    try {
      const client = createClient({
        chain: localnet,
        endpoint: 'http://studio.invalid/api',
      });
      const data = await client.getTransaction({ hash: parentHash });
      const wrapper = mountItem({ ...parentTransaction, data });
      await wrapper.trigger('click');

      const rendered = wrapper
        .findComponent({ name: 'JsonViewer' })
        .props('value');
      expect(rendered.consensus_data.leader_receipt).toHaveLength(1);
      expect(rendered.studio_data.leader_validation_receipt).toMatchObject({
        vote: 'agree',
        result: { raw: 'AXg=', status: 'rollback', payload: 'x' },
        calldata: { base64: 'AA==' },
        eq_outputs: { validator: { status: 'rollback', payload: 'x' } },
      });
      expect((data as { studio_data?: unknown }).studio_data).toEqual({
        leader_validation_receipt: validationReceipt,
      });
      wrapper.unmount();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  describe('equivalence principle outputs across leader rotations', () => {
    const node = { address: '0xabc' };
    const round = (name: string, leader: string, address = '0xabc') => ({
      consensus_round: name,
      status_changes: ['PENDING'],
      leader_result: [{ node_config: { address }, eq_outputs: { leader } }],
    });
    const outputsFor = async (data: any) => {
      const wrapper = mountItem({
        ...parentTransaction,
        data: { ...parentTransaction.data, ...data },
      });
      await wrapper.trigger('click');
      return wrapper
        .findAll('[data-testid="eq-output-round"]')
        .map((r) => r.find('pre').text());
    };

    it('shows archived rounds plus the active leader proposal', async () => {
      expect(
        await outputsFor({
          consensus_data: {
            leader_receipt: [
              { node_config: node, eq_outputs: { leader: '"third"' } },
            ],
          },
          consensus_history: {
            consensus_results: [
              round('Leader Rotation', '"first"'),
              round('Leader Rotation', '"second"'),
            ],
          },
        }),
      ).toEqual(['first', 'second', 'third']);
    });

    it('shows the proposal once after its round is archived', async () => {
      expect(
        await outputsFor({
          consensus_data: {
            leader_receipt: [
              { node_config: node, eq_outputs: { leader: '"third"' } },
            ],
          },
          consensus_history: {
            consensus_results: [
              round('Leader Rotation', '"first"'),
              round('Accepted', '"third"'),
            ],
          },
        }),
      ).toEqual(['first', 'third']);
    });
  });

  it('falls back to the current leader receipt without history', async () => {
    const wrapper = mountItem({
      ...parentTransaction,
      data: {
        ...parentTransaction.data,
        consensus_data: { leader_receipt: [{ eq_outputs: { leader: '"x"' } }] },
      },
    });
    await wrapper.trigger('click');
    expect(wrapper.findAll('[data-testid="eq-output-round"]')).toHaveLength(1);
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

  describe('refreshing a followed message', () => {
    const pendingChild = {
      hash: childHash,
      type: 2,
      statusName: 'PENDING',
      created_at: '2026-11-18T10:00:01Z',
      triggered_transactions: [],
    };
    const finalizedChild = {
      ...pendingChild,
      statusName: 'FINALIZED',
      consensus_data: {
        leader_receipt: [{ execution_result: 'SUCCESS' }],
      },
      triggered_transactions: [grandchildHash],
    };

    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    async function openChild(wrapper: ReturnType<typeof mountItem>) {
      await wrapper.trigger('click');
      await wrapper
        .find(`[data-testid="triggered-transaction-link-${childHash}"]`)
        .trigger('click');
      await vi.advanceTimersByTimeAsync(0);
    }

    it('updates status, result and newly emitted messages until final', async () => {
      getTransactionMock
        .mockResolvedValueOnce(pendingChild)
        .mockResolvedValueOnce(pendingChild)
        .mockResolvedValue(finalizedChild);

      const wrapper = mountItem();
      await openChild(wrapper);

      let child = wrapper.findAllComponents(TransactionItem)[0]!;
      expect(child.props('transaction').statusName).toBe('PENDING');
      expect(
        child
          .find(`[data-testid="triggered-transaction-link-${grandchildHash}"]`)
          .exists(),
      ).toBe(false);

      await vi.advanceTimersByTimeAsync(3_000);
      expect(getTransactionMock).toHaveBeenCalledTimes(2);
      child = wrapper.findAllComponents(TransactionItem)[0]!;
      expect(child.props('transaction').statusName).toBe('PENDING');

      await vi.advanceTimersByTimeAsync(3_000);
      expect(getTransactionMock).toHaveBeenCalledTimes(3);
      child = wrapper.findAllComponents(TransactionItem)[0]!;
      expect(child.props('transaction').statusName).toBe('FINALIZED');
      expect(child.text()).toContain('SUCCESS');
      expect(
        child
          .find(`[data-testid="triggered-transaction-link-${grandchildHash}"]`)
          .exists(),
      ).toBe(true);

      // A finalized transaction cannot change anymore: polling stops.
      await vi.advanceTimersByTimeAsync(30_000);
      expect(getTransactionMock).toHaveBeenCalledTimes(3);
    });

    it('stops refreshing when the followed message modal is closed', async () => {
      getTransactionMock.mockResolvedValue(pendingChild);

      const wrapper = mountItem();
      await openChild(wrapper);
      expect(getTransactionMock).toHaveBeenCalledTimes(1);

      const child = wrapper.findAllComponents(TransactionItem)[0]!;
      await child.findAll('.modal-close')[0]!.trigger('click');
      expect(wrapper.findAllComponents(TransactionItem)).toHaveLength(0);

      await vi.advanceTimersByTimeAsync(30_000);
      expect(getTransactionMock).toHaveBeenCalledTimes(1);
    });

    it('stops refreshing when the parent is unmounted', async () => {
      getTransactionMock.mockResolvedValue(pendingChild);

      const wrapper = mountItem();
      await openChild(wrapper);
      wrapper.unmount();

      await vi.advanceTimersByTimeAsync(30_000);
      expect(getTransactionMock).toHaveBeenCalledTimes(1);
    });
    function deferred<T>() {
      let resolve!: (value: T) => void;
      let reject!: (reason: unknown) => void;
      const promise = new Promise<T>((res, rej) => {
        resolve = res;
        reject = rej;
      });
      return { promise, resolve, reject };
    }

    it('does not start polling when unmounted during the initial fetch', async () => {
      const initial = deferred<typeof pendingChild>();
      getTransactionMock
        .mockReturnValueOnce(initial.promise)
        .mockResolvedValue(pendingChild);

      const wrapper = mountItem();
      await wrapper.trigger('click');
      await wrapper
        .find(`[data-testid="triggered-transaction-link-${childHash}"]`)
        .trigger('click');
      expect(getTransactionMock).toHaveBeenCalledTimes(1);

      wrapper.unmount();
      initial.resolve(pendingChild);
      await vi.advanceTimersByTimeAsync(30_000);

      expect(getTransactionMock).toHaveBeenCalledTimes(1);
    });

    it.each([
      ['resolves', (d: ReturnType<typeof deferred>) => d.resolve(pendingChild)],
      [
        'rejects',
        (d: ReturnType<typeof deferred>) => d.reject(new Error('network')),
      ],
    ])(
      'does not resume polling when unmounted while a poll is in flight and it %s',
      async (_label, settle) => {
        const poll = deferred<typeof pendingChild>();
        getTransactionMock
          .mockResolvedValueOnce(pendingChild)
          .mockReturnValueOnce(poll.promise)
          .mockResolvedValue(pendingChild);
        const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});

        const wrapper = mountItem();
        await openChild(wrapper);
        await vi.advanceTimersByTimeAsync(3_000);
        expect(getTransactionMock).toHaveBeenCalledTimes(2);

        wrapper.unmount();
        settle(poll as ReturnType<typeof deferred>);
        await vi.advanceTimersByTimeAsync(30_000);

        expect(getTransactionMock).toHaveBeenCalledTimes(2);
        warn.mockRestore();
      },
    );

    it('does not resume polling when the modal is closed while a poll is in flight', async () => {
      const poll = deferred<typeof pendingChild>();
      getTransactionMock
        .mockResolvedValueOnce(pendingChild)
        .mockReturnValueOnce(poll.promise)
        .mockResolvedValue(pendingChild);

      const wrapper = mountItem();
      await openChild(wrapper);
      await vi.advanceTimersByTimeAsync(3_000);
      expect(getTransactionMock).toHaveBeenCalledTimes(2);

      const child = wrapper.findAllComponents(TransactionItem)[0]!;
      await child.findAll('.modal-close')[0]!.trigger('click');
      poll.resolve(pendingChild);
      await vi.advanceTimersByTimeAsync(30_000);

      expect(getTransactionMock).toHaveBeenCalledTimes(2);
      expect(wrapper.findAllComponents(TransactionItem)).toHaveLength(0);
    });
    function parentCloseButton(wrapper: ReturnType<typeof mountItem>) {
      // The first close button belongs to the parent (root) modal.
      return wrapper.findAll('.modal-close')[0]!;
    }

    it('abandons the followed message when the parent closes during the initial fetch', async () => {
      const initial = deferred<typeof pendingChild>();
      getTransactionMock
        .mockReturnValueOnce(initial.promise)
        .mockResolvedValue(pendingChild);

      const wrapper = mountItem();
      await wrapper.trigger('click');
      await wrapper
        .find(`[data-testid="triggered-transaction-link-${childHash}"]`)
        .trigger('click');
      expect(getTransactionMock).toHaveBeenCalledTimes(1);

      await parentCloseButton(wrapper).trigger('click');
      expect(wrapper.find('.modal').exists()).toBe(false);

      initial.resolve(pendingChild);
      await vi.advanceTimersByTimeAsync(30_000);
      expect(getTransactionMock).toHaveBeenCalledTimes(1);

      // Reopening the parent does not resurrect the abandoned child, and its
      // messages can be followed again.
      await wrapper.trigger('click');
      expect(wrapper.findAllComponents(TransactionItem)).toHaveLength(0);
      const link = wrapper.find(
        `[data-testid="triggered-transaction-link-${childHash}"]`,
      );
      expect(link.attributes('disabled')).toBeUndefined();

      await link.trigger('click');
      await vi.advanceTimersByTimeAsync(0);
      expect(getTransactionMock).toHaveBeenCalledTimes(2);
      expect(wrapper.findAllComponents(TransactionItem)).toHaveLength(1);
    });

    it('stops polling the followed message when the parent closes', async () => {
      const poll = deferred<typeof pendingChild>();
      getTransactionMock
        .mockResolvedValueOnce(pendingChild)
        .mockReturnValueOnce(poll.promise)
        .mockResolvedValue(pendingChild);

      const wrapper = mountItem();
      await openChild(wrapper);
      await vi.advanceTimersByTimeAsync(3_000);
      expect(getTransactionMock).toHaveBeenCalledTimes(2);

      await parentCloseButton(wrapper).trigger('click');
      poll.resolve(pendingChild);
      await vi.advanceTimersByTimeAsync(30_000);
      expect(getTransactionMock).toHaveBeenCalledTimes(2);

      await wrapper.trigger('click');
      expect(wrapper.findAllComponents(TransactionItem)).toHaveLength(0);
    });
  });
});
