import { flushPromises, mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import SimulatorSection from '@/components/Simulator/settings/SimulatorSection.vue';

const mocks = vi.hoisted(() => ({
  resetStudioState: vi.fn(),
  clearTransactionsForCurrentNetwork: vi.fn(),
  clearDeployedContractsForCurrentNetwork: vi.fn(),
  notify: vi.fn(),
}));

vi.mock('@/hooks', () => ({
  useConfig: vi.fn(() => ({ canResetStudioState: { value: true } })),
  useRpcClient: vi.fn(() => ({ resetStudioState: mocks.resetStudioState })),
  useSetupStores: vi.fn(() => ({ setupStores: vi.fn() })),
}));

vi.mock('@/stores', () => ({
  useTransactionsStore: vi.fn(() => ({
    clearTransactionsForCurrentNetwork:
      mocks.clearTransactionsForCurrentNetwork,
    resetStorage: vi.fn(),
  })),
  useContractsStore: vi.fn(() => ({
    clearDeployedContractsForCurrentNetwork:
      mocks.clearDeployedContractsForCurrentNetwork,
    resetStorage: vi.fn(),
  })),
}));

vi.mock('@kyvg/vue3-notification', () => ({ notify: mocks.notify }));

const mountSection = () =>
  mount(SimulatorSection, {
    global: {
      stubs: {
        PageSection: { template: '<div><slot /></div>' },
        Btn: { template: '<button><slot /></button>' },
        Alert: { template: '<div><slot /></div>' },
        RouterLink: { template: '<a><slot /></a>' },
        ConfirmationModal: {
          props: ['buttonTestId'],
          emits: ['confirm', 'close'],
          template:
            '<div><button :data-testid="buttonTestId" @click="$emit(\'confirm\')" /><slot /></div>',
        },
      },
    },
  });

describe('SimulatorSection reset transactions & accounts', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('clears local state and reports success once the backend confirmed', async () => {
    mocks.resetStudioState.mockResolvedValueOnce({
      deleted_transactions: 4,
      deleted_accounts: 2,
      workers_reset: 1,
      tasks_cancelled: 1,
    });
    const wrapper = mountSection();

    await wrapper
      .find('[data-testid="btn-reset-studio-state"]')
      .trigger('click');
    await flushPromises();

    expect(mocks.clearTransactionsForCurrentNetwork).toHaveBeenCalled();
    expect(mocks.clearDeployedContractsForCurrentNetwork).toHaveBeenCalled();
    expect(mocks.notify).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'success' }),
    );
  });

  it('surfaces a backend failure and keeps local state', async () => {
    mocks.resetStudioState.mockRejectedValueOnce(
      new Error(
        '1 of 2 consensus workers did not confirm the reset within 30s. Nothing was deleted; please try again.',
      ),
    );
    const wrapper = mountSection();

    await wrapper
      .find('[data-testid="btn-reset-studio-state"]')
      .trigger('click');
    await flushPromises();

    expect(mocks.clearTransactionsForCurrentNetwork).not.toHaveBeenCalled();
    expect(
      mocks.clearDeployedContractsForCurrentNetwork,
    ).not.toHaveBeenCalled();
    expect(mocks.notify).toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'error',
        text: expect.stringContaining('did not confirm the reset'),
      }),
    );
  });
});
