import { shallowMount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Header from '@/components/Header.vue';

vi.mock('@/stores', () => ({
  useUIStore: () => ({
    mode: 'light',
    toggleMode: vi.fn(),
    runTutorial: vi.fn(),
  }),
}));

describe('Header release version', () => {
  beforeEach(() => {
    delete window.__RUNTIME_CONFIG__;
    vi.unstubAllEnvs();
  });

  afterEach(() => {
    delete window.__RUNTIME_CONFIG__;
    vi.unstubAllEnvs();
  });

  it('shows the deployed release version from runtime configuration', () => {
    window.__RUNTIME_CONFIG__ = {
      VITE_APP_VERSION: 'v0.123.0-rc.1',
    };

    const wrapper = shallowMount(Header, {
      global: {
        directives: { tooltip: () => undefined },
        stubs: { RouterLink: true },
      },
    });

    expect(wrapper.get('[data-testid="app-version"]').text()).toBe(
      'v0.123.0-rc.1',
    );
  });

  it('does not render an empty version badge for local builds', () => {
    vi.stubEnv('VITE_APP_VERSION', '');

    const wrapper = shallowMount(Header, {
      global: {
        directives: { tooltip: () => undefined },
        stubs: { RouterLink: true },
      },
    });

    expect(wrapper.find('[data-testid="app-version"]').exists()).toBe(false);
  });
});
