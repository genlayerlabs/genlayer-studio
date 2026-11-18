/**
 * Input-to-toast flow for calldata encoding errors (DXP-583).
 *
 * Mounts the real parameter form (ContractParams.vue + AnyField.vue) inside
 * ContractMethodItem / ConstructorParameters; only the network-facing hook
 * (useContractQueries) and the toast library are mocked.
 */
import { flushPromises, mount } from '@vue/test-utils';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { defineComponent, h, ref } from 'vue';
import type { ContractMethod } from 'genlayer-js/types';
import ContractMethodItem from '@/components/Simulator/ContractMethodItem.vue';
import ConstructorParameters from '@/components/Simulator/ConstructorParameters.vue';
import AnyField from '@/components/global/fields/AnyField.vue';
import StringField from '@/components/global/fields/StringField.vue';

const mocks = vi.hoisted(() => ({
  callWriteMethod: vi.fn(),
  callReadMethod: vi.fn(),
  simulateWriteMethod: vi.fn(),
  estimateWriteMethodFees: vi.fn(),
  deployContract: vi.fn(),
  notify: vi.fn(),
  // Filled after imports; resolved lazily to avoid a circular @/hooks import
  fields: {} as Record<string, unknown>,
}));

vi.mock('@/hooks', () => {
  let id = 0;
  return {
    useInputMap: () => ({
      getComponent: (type: string) => mocks.fields[type] ?? mocks.fields.any,
    }),
    useUniqueId: (prefix: string) => `${prefix}-${++id}`,
    useEventTracking: () => ({ trackEvent: vi.fn() }),
    useContractQueries: () => ({
      callWriteMethod: mocks.callWriteMethod,
      callReadMethod: mocks.callReadMethod,
      simulateWriteMethod: mocks.simulateWriteMethod,
      estimateWriteMethodFees: mocks.estimateWriteMethodFees,
      deployContract: mocks.deployContract,
      isDeploying: ref(false),
      contract: ref({ name: 'Storage' }),
      contractSchemaQuery: {
        data: ref({
          ctor: { params: [['owner', 'any']], kwparams: {} },
        }),
        isPending: ref(false),
        isRefetching: ref(false),
        isError: ref(false),
      },
    }),
  };
});

vi.mock('@kyvg/vue3-notification', () => ({ notify: mocks.notify }));

vi.mock('vue-collapsed', () => ({
  Collapse: {
    props: ['when'],
    template: '<div v-if="when"><slot /></div>',
  },
}));

vi.mock('genlayer-js', () => ({
  abi: { calldata: { toString: vi.fn((value) => String(value)) } },
}));

vi.mock('genlayer-js/types', async () => ({
  ...(await vi.importActual<object>('genlayer-js/types')),
  TransactionHashVariant: {
    LATEST_FINAL: 'latest-final',
    LATEST_NONFINAL: 'latest-nonfinal',
  },
}));

mocks.fields.any = AnyField;
mocks.fields.string = StringField;

const Btn = defineComponent({
  inheritAttrs: false,
  props: ['testId'],
  emits: ['click'],
  setup(props, { emit, slots, attrs }) {
    return () =>
      h(
        'button',
        {
          'data-testid': props.testId ?? attrs['data-testid'],
          onClick: () => emit('click'),
        },
        slots.default?.(),
      );
  },
});

const stubs = {
  Btn,
  PageSection: { template: '<div><slot name="title" /><slot /></div>' },
  EmptyListPlaceholder: { template: '<div><slot /></div>' },
  Loader: true,
  ContentLoader: true,
  Alert: true,
};

const method = {
  params: [['recipient', 'any']],
  kwparams: {},
  readonly: false,
  ret: 'null',
  payable: false,
} as unknown as ContractMethod;

const mountMethod = async (methodType: 'read' | 'write') => {
  const wrapper = mount(ContractMethodItem, {
    props: {
      name: 'send',
      method,
      methodType,
      executionMode: 'NORMAL',
    },
    global: { stubs },
  });
  await wrapper.find('[data-testid="expand-method-btn-send"]').trigger('click');
  return wrapper;
};

const errorToast = (text: string) =>
  expect.objectContaining({ type: 'error', text });

describe('calldata encoding errors: input to toast', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('write: invalid input shows inline + toast errors and sends no transaction', async () => {
    const wrapper = await mountMethod('write');
    await wrapper.find('input').setValue('[1, 2');

    expect(wrapper.find('[data-testid="field-error-recipient"]').text()).toBe(
      'unexpected end of input',
    );

    await wrapper
      .find('[data-testid="write-method-btn-send"]')
      .trigger('click');
    await flushPromises();

    expect(mocks.callWriteMethod).not.toHaveBeenCalled();
    expect(mocks.notify).toHaveBeenCalledWith(
      errorToast(
        'Invalid value for parameter "recipient": unexpected end of input',
      ),
    );
  });

  it('write: valid input sends the encoded transaction', async () => {
    mocks.callWriteMethod.mockResolvedValueOnce(true);
    const wrapper = await mountMethod('write');
    await wrapper.find('input').setValue('[1, 2]');
    expect(wrapper.find('[data-testid="field-error-recipient"]').exists()).toBe(
      false,
    );

    await wrapper
      .find('[data-testid="write-method-btn-send"]')
      .trigger('click');
    await flushPromises();

    expect(mocks.callWriteMethod).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'send',
        args: { args: [[1n, 2n]], kwargs: {} },
      }),
    );
    expect(mocks.notify).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'success' }),
    );
  });

  it('write: SDK encoding rejection is shown in the toast', async () => {
    mocks.callWriteMethod.mockRejectedValueOnce(
      new Error('invalid calldata: unsupported type'),
    );
    const wrapper = await mountMethod('write');
    await wrapper.find('input').setValue('1');
    await wrapper
      .find('[data-testid="write-method-btn-send"]')
      .trigger('click');
    await flushPromises();

    expect(mocks.notify).toHaveBeenCalledWith(
      errorToast('invalid calldata: unsupported type'),
    );
  });

  it('read: invalid input shows a toast and does not call the contract', async () => {
    const wrapper = await mountMethod('read');
    await wrapper.find('input').setValue('oops');
    await wrapper.find('[data-testid="read-method-btn-send"]').trigger('click');
    await flushPromises();

    expect(mocks.callReadMethod).not.toHaveBeenCalled();
    expect(mocks.notify).toHaveBeenCalledWith(
      errorToast(
        'Invalid value for parameter "recipient": Unable to consume token: oops (at column 1)',
      ),
    );
  });

  it('deploy: invalid constructor input shows a toast and does not deploy', async () => {
    const wrapper = mount(ConstructorParameters, {
      props: { executionMode: 'NORMAL', consensusMaxRotations: 3 },
      global: { stubs },
    });
    await flushPromises();
    await wrapper.find('input').setValue('addr#zz');
    expect(wrapper.find('[data-testid="field-error-owner"]').exists()).toBe(
      true,
    );

    await wrapper.find('[data-testid="btn-deploy-contract"]').trigger('click');
    await flushPromises();

    expect(mocks.deployContract).not.toHaveBeenCalled();
    expect(mocks.notify).toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'error',
        text: expect.stringMatching(/^Invalid value for parameter "owner": /),
      }),
    );
    expect(wrapper.emitted('deployed-contract')).toBeUndefined();
  });

  it('deploy: valid constructor input deploys with encoded args', async () => {
    mocks.deployContract.mockResolvedValueOnce({});
    const wrapper = mount(ConstructorParameters, {
      props: { executionMode: 'NORMAL', consensusMaxRotations: 3 },
      global: { stubs },
    });
    await flushPromises();
    await wrapper.find('input').setValue('"alice"');
    await wrapper.find('[data-testid="btn-deploy-contract"]').trigger('click');
    await flushPromises();

    expect(mocks.deployContract).toHaveBeenCalledWith(
      { args: ['alice'], kwargs: {} },
      'NORMAL',
      3,
    );
    expect(mocks.notify).not.toHaveBeenCalled();
  });
});
