import { mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ConsensusFeeSection from '@/components/Simulator/settings/ConsensusFeeSection.vue';
import { loadFeeSettings, saveFeeSettings } from '@/utils/consensusFees';

let fail = false;
const policy = {
  minProposeTimeunits: '30',
  maxProposeTimeunits: '600',
  minCommitTimeunits: '30',
  maxCommitTimeunits: '600',
};
vi.mock('@/hooks', () => ({
  useRpcClient: () => ({
    getFeeConfig: async () => {
      if (fail) throw new Error('down');
      return { policy };
    },
  }),
}));

vi.mock('@/stores', () => ({
  useConsensusStore: () => ({
    feeSettings: loadFeeSettings(),
    setFeeSettings: saveFeeSettings,
  }),
}));

describe('ConsensusFeeSection', () => {
  beforeEach(() => {
    localStorage.clear();
    fail = false;
    vi.unstubAllEnvs();
  });

  it('uses environment defaults and persists the saved fee settings across mounts', async () => {
    vi.stubEnv('VITE_LEADER_TIMEOUT_FEE', '120');
    const settings = mount(ConsensusFeeSection, {
      global: { stubs: ['Loader'] },
    });
    const [leader, validator, appeals, rotations] = settings.findAll('input');

    expect(leader.element.value).toBe('120');
    await validator.setValue('140');
    await appeals.setValue('1');
    await rotations.setValue('0, 2');
    await settings.get('form').trigger('submit');
    expect(settings.get('[role="status"]').text()).toBe('Saved');

    const reopened = mount(ConsensusFeeSection, {
      global: { stubs: ['Loader'] },
    });
    expect(
      reopened.findAll('input').map((input) => input.element.value),
    ).toEqual(['120', '140', '1', '0, 2']);
  });

  it('rejects a negative value and a schedule whose length does not match the appeal rounds', async () => {
    const settings = mount(ConsensusFeeSection, {
      global: { stubs: ['Loader'] },
    });
    const [leader, , appeals, rotations] = settings.findAll('input');

    await leader.setValue('-1');
    await settings.get('form').trigger('submit');
    expect(settings.get('[role="alert"]').text()).toContain('Leader timeout');
    expect(loadFeeSettings().leaderTimeunitsAllocation).toBe('');

    await leader.setValue('120');
    await appeals.setValue('2');
    await rotations.setValue('0, 2');
    await settings.get('form').trigger('submit');
    expect(settings.get('[role="alert"]').text()).toContain(
      'each appeal round',
    );
    expect(loadFeeSettings().appealRounds).toBe('');
  });

  it('rejects timeouts outside the active policy bounds', async () => {
    const settings = mount(ConsensusFeeSection, {
      global: { stubs: ['Loader'] },
    });
    await new Promise((r) => setTimeout(r));
    await settings.findAll('input')[0].setValue('1');
    await settings.get('form').trigger('submit');
    expect(settings.get('[role="alert"]').text()).toContain(
      'between 30 and 600',
    );
    expect(loadFeeSettings().leaderTimeunitsAllocation).toBe('');
  });

  it('reports when the browser blocks persistence', async () => {
    const spy = vi
      .spyOn(Storage.prototype, 'setItem')
      .mockImplementation(() => {
        throw new Error('denied');
      });
    const settings = mount(ConsensusFeeSection, {
      global: { stubs: ['Loader'] },
    });
    await settings.findAll('input')[0].setValue('120');
    await settings.get('form').trigger('submit');
    spy.mockRestore();
    expect(settings.get('[role="alert"]').text()).toContain('blocked saving');
    expect(settings.find('[role="status"]').exists()).toBe(false);
  });

  it('drops an inconsistent environment schedule when loading', () => {
    vi.stubEnv('VITE_APPEAL_ROUNDS_FEE', '2');
    vi.stubEnv('VITE_ROTATIONS_FEE', '0,1');
    const loaded = loadFeeSettings();
    expect(loaded.appealRounds).toBe('');
    expect(loaded.rotations).toBe('');
  });

  it('does not save timeouts while the policy is unavailable', async () => {
    fail = true;
    const settings = mount(ConsensusFeeSection, {
      global: { stubs: ['Loader'] },
    });
    await new Promise((r) => setTimeout(r));
    await settings.findAll('input')[0].setValue('1');
    await settings.get('form').trigger('submit');
    await new Promise((r) => setTimeout(r));
    fail = false;
    expect(settings.get('[role="alert"]').text()).toContain(
      'Could not load the fee policy',
    );
    expect(settings.find('[role="status"]').exists()).toBe(false);
    expect(loadFeeSettings().leaderTimeunitsAllocation).toBe('');
  });
});
