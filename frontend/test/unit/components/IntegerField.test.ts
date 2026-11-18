import { mount } from '@vue/test-utils';
import { abi } from 'genlayer-js';
import { defineComponent, h, ref } from 'vue';
import { describe, expect, it } from 'vitest';
import IntegerField from '@/components/global/fields/IntegerField.vue';
import { unfoldArgsData } from '@/components/Simulator/ContractParams';

function mountField(initial: unknown = 0) {
  const value = ref<unknown>(initial);
  const Host = defineComponent({
    setup: () => () =>
      h(IntegerField, {
        name: 'amount',
        label: 'amount',
        testId: 'amount-input',
        modelValue: value.value,
        'onUpdate:modelValue': (v: unknown) => {
          value.value = v;
        },
      }),
  });
  const wrapper = mount(Host);
  return { wrapper, value, input: () => wrapper.get('input') };
}

// what the contract receives when this value is sent as its only argument
function received(val: unknown): unknown {
  const { args } = unfoldArgsData({
    args: [{ val: val as never, key: 0 }],
    kwargs: {},
  });
  const calldata = abi.calldata.encode(
    abi.calldata.makeCalldataObject('transfer', args, undefined),
  );
  return (abi.calldata.decode(calldata) as Map<string, unknown[]>).get(
    'args',
  )![0];
}

describe('IntegerField', () => {
  it('sends exactly the integer that was typed above 2^53', async () => {
    const { value, input } = mountField();
    await input().setValue('12345678901234567891');

    expect(typeof value.value).toBe('bigint');
    expect(received(value.value)).toBe(12345678901234567891n);
  });

  it('sends the maximum u256 exactly', async () => {
    const { value, input } = mountField();
    await input().setValue((2n ** 256n - 1n).toString());

    expect(received(value.value)).toBe(2n ** 256n - 1n);
  });

  it('sends negative integers above 2^53 exactly', async () => {
    const { value, input } = mountField();
    await input().setValue('-9007199254740993');

    expect(received(value.value)).toBe(-9007199254740993n);
  });

  it('shows the default value', () => {
    const { input } = mountField(0);
    expect((input().element as HTMLInputElement).value).toBe('0');
  });

  it('is a text input, a number input would turn the value into a JS number', () => {
    const { input } = mountField();
    expect(input().attributes('type')).toBe('text');
  });

  it('keeps the data-testid and the name', () => {
    const { input } = mountField();
    expect(input().attributes('data-testid')).toBe('amount-input');
    expect(input().attributes('name')).toBe('amount');
  });

  it('sends small integers as before', async () => {
    const { value, input } = mountField();
    await input().setValue('42');

    expect(received(value.value)).toBe(42n);
  });

  it('does not rewrite what is being typed', async () => {
    const { input } = mountField();
    await input().setValue('0012');
    expect((input().element as HTMLInputElement).value).toBe('0012');
  });

  it('keeps an empty field empty, as before', async () => {
    const { value, input } = mountField();
    await input().setValue('');
    expect(value.value).toBe('');
  });

  it('accepts exponent notation that is a safe integer, as before', async () => {
    const { value, input } = mountField();
    await input().setValue('1e5');
    expect(received(value.value)).toBe(100000n);
  });

  it('still refuses a fraction', async () => {
    const { value, input } = mountField();
    await input().setValue('1.5');
    expect(() => received(value.value)).toThrow();
  });

  it('refuses an integer written as 1e30 instead of sending a rounded one', async () => {
    const { value, input } = mountField();
    await input().setValue('1e30');
    expect(() => received(value.value)).toThrow();
  });

  it('marks text that is not a number as invalid', async () => {
    const { input } = mountField();
    await input().setValue('abc');
    expect(input().classes()).toContain('ring-red-600/80');

    await input().setValue('12');
    expect(input().classes()).not.toContain('ring-red-600/80');
  });

  it('follows the value when it is changed from outside', async () => {
    const { wrapper, value, input } = mountField();
    await input().setValue('12345678901234567891');

    value.value = 0;
    await wrapper.vm.$nextTick();

    expect((input().element as HTMLInputElement).value).toBe('0');
  });

  it('shows a bigint that is set from outside', async () => {
    const { wrapper, value, input } = mountField();
    value.value = 12345678901234567891n;
    await wrapper.vm.$nextTick();

    expect((input().element as HTMLInputElement).value).toBe(
      '12345678901234567891',
    );
  });
});
