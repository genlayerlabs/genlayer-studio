import { mount } from '@vue/test-utils';
import { describe, expect, it, vi } from 'vitest';
import TransactionItem from '@/components/Simulator/TransactionItem.vue';

vi.mock('@/stores', () => ({
  useUIStore: vi.fn(() => ({ mode: 'light' })),
  useNodeStore: vi.fn(() => ({ searchFilter: '' })),
  useTransactionsStore: vi.fn(() => ({
    cancelTransaction: vi.fn(),
    setTransactionAppeal: vi.fn(),
    getTransaction: vi.fn(),
  })),
}));
vi.mock('@kyvg/vue3-notification', () => ({ notify: vi.fn() }));
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

const ModalStub = {
  props: ['open'],
  emits: ['close'],
  template: '<section v-if="open"><slot name="title" /><slot /></section>',
};

describe('TransactionItem single-line scrollable blocks', () => {
  it('reserves bottom padding so the horizontal scrollbar does not cover the text', async () => {
    const hash = `0x${'a'.repeat(64)}`;
    const wrapper = mount(TransactionItem, {
      props: {
        transaction: {
          hash,
          type: 'method',
          statusName: 'FINALIZED',
          contractAddress: '0x1111111111111111111111111111111111111111',
          localContractId: 'c1',
          data: {
            hash,
            created_at: '2026-11-18T10:00:00Z',
            statusName: 'FINALIZED',
            data: { calldata: { readable: 'x'.repeat(500) } },
          },
        } as any,
        finalityWindow: 60,
      },
      global: {
        directives: { tooltip: vi.fn() },
        stubs: {
          Modal: ModalStub,
          Btn: true,
          CopyTextButton: true,
          JsonViewer: true,
          Loader: true,
          TransactionStatusBadge: true,
        },
      },
    });
    await wrapper.trigger('click');

    const pres = wrapper
      .findAll('pre')
      .filter((p) => p.classes().includes('overflow-x-auto'));
    expect(pres.length).toBeGreaterThan(0);
    for (const pre of pres) {
      expect(pre.classes()).toContain('pb-3');
    }
  });
});
