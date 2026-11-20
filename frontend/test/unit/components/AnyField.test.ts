import { mount } from '@vue/test-utils';
import { describe, it, expect, vi } from 'vitest';
import AnyField from '@/components/global/fields/AnyField.vue';
import { AnyFieldValue } from '@/components/global/fields/AnyFieldValue';

vi.mock('@/hooks', () => ({
  useUniqueId: (prefix: string) => `${prefix}-1`,
}));

const mountField = (value: string) =>
  mount(AnyField, {
    props: {
      name: 'data',
      label: 'data',
      modelValue: new AnyFieldValue(value),
    },
  });

describe('AnyField', () => {
  it('shows no error for valid or empty input', () => {
    expect(
      mountField('[1, 2]').find('[data-testid="field-error-data"]').exists(),
    ).toBe(false);
    expect(
      mountField('').find('[data-testid="field-error-data"]').exists(),
    ).toBe(false);
  });

  it('shows an inline error when the value cannot be encoded', () => {
    const wrapper = mountField('{a: ');
    const error = wrapper.find('[data-testid="field-error-data"]');
    expect(error.exists()).toBe(true);
    expect(error.text()).toBe('unexpected end of input');
    expect(wrapper.find('input').classes()).toContain('ring-red-600/80');
  });

  it('updates the error as the user types', async () => {
    const wrapper = mountField('');
    await wrapper.find('input').setValue('oops');
    await wrapper.setProps({
      modelValue: wrapper.emitted('update:modelValue')!.at(-1)![0],
    });
    expect(wrapper.find('[data-testid="field-error-data"]').text()).toContain(
      'Unable to consume token: oops',
    );
    await wrapper.find('input').setValue('"oops"');
    await wrapper.setProps({
      modelValue: wrapper.emitted('update:modelValue')!.at(-1)![0],
    });
    expect(wrapper.find('[data-testid="field-error-data"]').exists()).toBe(
      false,
    );
  });
});
