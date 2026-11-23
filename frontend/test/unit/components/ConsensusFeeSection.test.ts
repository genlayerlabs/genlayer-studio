import { mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ConsensusFeeSection from '@/components/Simulator/settings/ConsensusFeeSection.vue';
import { loadFeeSettings, saveFeeSettings } from '@/utils/consensusFees';

vi.mock('@/stores', () => ({
  useConsensusStore: () => ({
    feeSettings: loadFeeSettings(),
    setFeeSettings: saveFeeSettings,
  }),
}));

describe('ConsensusFeeSection', () => {
  beforeEach(() => {
    localStorage.clear();
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
});
