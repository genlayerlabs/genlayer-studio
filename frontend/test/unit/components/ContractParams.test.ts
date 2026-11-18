import { flushPromises, mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import type { ContractParamsSchema } from 'genlayer-js/types';
import ContractParams from '@/components/Simulator/ContractParams.vue';
import {
  unfoldArgsData,
  type ArgData,
} from '@/components/Simulator/ContractParams';

describe('union contract parameters', () => {
  async function setup(type: ContractParamsSchema, keyword = false) {
    const wrapper = mount(ContractParams, {
      props: {
        methodBase: {
          params: keyword ? [] : [['a', type]],
          kwparams: keyword ? { a: type } : {},
        },
      },
      global: { directives: { tooltip: () => {} } },
    });
    await flushPromises();
    const values = () =>
      unfoldArgsData(wrapper.emitted('argsChanged')!.at(-1)![0] as ArgData);
    const value = () => (keyword ? values().kwargs.a : values().args[0]);
    return { wrapper, value };
  }

  it.each([false, true])(
    'encodes nullable strings, including empty strings (keyword=%s)',
    async (keyword) => {
      const { wrapper, value } = await setup(
        { $or: ['string', 'null'] },
        keyword,
      );
      expect(value()).toBeNull();
      await wrapper.get('select').setValue('0');
      expect(value()).toBe('');
      await wrapper.get('input').setValue('hello without calldata quotes');
      expect(value()).toBe('hello without calldata quotes');
      await wrapper.get('select').setValue('1');
      expect(value()).toBeNull();
      expect(wrapper.find('input').exists()).toBe(false);
    },
  );

  it('keeps string and integer alternatives distinct', async () => {
    const { wrapper, value } = await setup({ $or: ['string', 'int', 'bool'] });
    await wrapper.get('input').setValue('42');
    expect(value()).toBe('42');
    await wrapper.get('select').setValue('1');
    expect(value()).toBe(0);
    await wrapper.get('input').setValue('42');
    expect(value()).toBe(42);
    await wrapper.get('select').setValue('2');
    expect(value()).toBe(false);
    await wrapper.get('input').setValue(true);
    expect(value()).toBe(true);
  });

  it('uses the calldata parser for complex alternatives', async () => {
    const { wrapper, value } = await setup({ $or: ['array', 'null'] });
    await wrapper.get('select').setValue('0');
    await wrapper.get('input').setValue('[1, 2]');
    expect(value()).toEqual([1n, 2n]);
  });

  it('resets the selected type and value when the method changes', async () => {
    const { wrapper, value } = await setup({ $or: ['string', 'null'] });
    await wrapper.get('select').setValue('0');
    await wrapper.get('input').setValue('old');
    await wrapper.setProps({
      methodBase: { params: [['a', { $or: ['bool', 'null'] }]], kwparams: {} },
    });
    await flushPromises();
    expect(value()).toBeNull();
    expect(wrapper.get('select').element.value).toBe('1');
  });
});
