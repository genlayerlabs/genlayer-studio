import { flushPromises, mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import FundAccountPopover from '@/components/Simulator/FundAccountPopover.vue';
import Btn from '@/components/global/Btn.vue';
import Loader from '@/components/global/Loader.vue';
import type { AccountInfo } from '@/stores';

const mocks = vi.hoisted(() => ({
  fundAccount: vi.fn(),
  refreshBalance: vi.fn(),
  notify: vi.fn(),
}));

vi.mock('@/stores', () => ({
  useAccountsStore: () => ({ ...mocks, balances: {}, funding: {} }),
}));

vi.mock('@kyvg/vue3-notification', () => ({ notify: mocks.notify }));
vi.mock('floating-vue', () => ({
  Dropdown: { template: '<div><slot /><slot name="popper" /></div>' },
}));

const account: AccountInfo = {
  type: 'local',
  address: '0x2222222222222222222222222222222222222222',
};

const mountPopover = () =>
  mount(FundAccountPopover, {
    props: { account },
    global: { components: { Btn, Loader } },
  });

describe('FundAccountPopover', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    mocks.fundAccount.mockResolvedValue(true);
  });

  it('funds the target account immediately with a preset amount in decimal wei', async () => {
    const wrapper = mountPopover();
    const maxPreset = wrapper.get('form button[type="button"]:last-of-type');
    expect(maxPreset.text()).toBe('+10000');

    await maxPreset.trigger('click');
    await flushPromises();

    expect(mocks.fundAccount).toHaveBeenCalledExactlyOnceWith(
      account,
      '10000000000000000000000',
    );
    wrapper.unmount();
  });

  it('shows a validation error for an invalid custom amount', async () => {
    const wrapper = mountPopover();
    expect(wrapper.text()).toContain('Current: unknown');
    await wrapper.get('input').setValue('0');
    await wrapper.get('form').trigger('submit');

    expect(wrapper.get('[role="alert"]').text()).toContain(
      'Enter a positive amount',
    );
    expect(mocks.fundAccount).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it('keeps the amount and skips the success toast when funding is skipped', async () => {
    mocks.fundAccount.mockResolvedValueOnce(false);
    const wrapper = mountPopover();
    const input = wrapper.get('input');
    await input.setValue('1');
    await wrapper.get('form').trigger('submit');
    await flushPromises();

    expect(mocks.fundAccount).toHaveBeenCalledExactlyOnceWith(
      account,
      '1000000000000000000',
    );
    expect(input.element.value).toBe('1');
    expect(mocks.notify).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it('shows the RPC error and keeps the entered amount available for retry', async () => {
    mocks.fundAccount.mockRejectedValueOnce(new Error('Faucet unavailable'));
    const wrapper = mountPopover();
    const input = wrapper.get('input');
    await input.setValue('0.000000000000000001');
    await wrapper.get('form').trigger('submit');
    await flushPromises();

    expect(mocks.fundAccount).toHaveBeenCalledExactlyOnceWith(account, '1');
    expect(wrapper.get('[role="alert"]').text()).toBe('Faucet unavailable');
    expect(input.element.value).toBe('0.000000000000000001');
    expect(mocks.notify).not.toHaveBeenCalled();
    wrapper.unmount();
  });
});
