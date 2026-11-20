import { flushPromises, mount } from '@vue/test-utils';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { defineComponent, h, ref } from 'vue';
import ConstructorParameters from '@/components/Simulator/ConstructorParameters.vue';
import { AnyFieldValue } from '@/components/global/fields/AnyFieldValue';

const mocks = vi.hoisted(() => ({
  deployContract: vi.fn(),
  notify: vi.fn(),
  emittedArgs: undefined as unknown,
}));

vi.mock('@/hooks', () => ({
  useContractQueries: vi.fn(() => ({
    contract: ref({ name: 'Storage' }),
    contractSchemaQuery: {
      data: ref({ ctor: { params: [['owner', 'any']], kwparams: {} } }),
      isPending: ref(false),
      isRefetching: ref(false),
      isError: ref(false),
    },
    deployContract: mocks.deployContract,
    isDeploying: ref(false),
  })),
}));

vi.mock('@kyvg/vue3-notification', () => ({ notify: mocks.notify }));

// Stub the params form so the test controls the argument values directly
vi.mock('@/components/Simulator/ContractParams.vue', () => ({
  default: defineComponent({
    emits: ['argsChanged'],
    mounted() {
      this.$emit('argsChanged', mocks.emittedArgs);
    },
    render: () => h('div'),
  }),
}));

const BtnStub = defineComponent({
  emits: ['click'],
  setup(_, { emit, slots }) {
    return () =>
      h(
        'button',
        { 'data-testid': 'deploy', onClick: () => emit('click') },
        slots.default?.(),
      );
  },
});

const mountComponent = () =>
  mount(ConstructorParameters, {
    props: { executionMode: 'NORMAL', consensusMaxRotations: 3 },
    global: {
      stubs: {
        PageSection: { template: '<div><slot name="title" /><slot /></div>' },
        Btn: BtnStub,
        Loader: true,
        ContentLoader: true,
        Alert: true,
      },
    },
  });

describe('ConstructorParameters deploy', () => {
  beforeEach(() => {
    mocks.deployContract.mockReset();
    mocks.notify.mockReset();
  });

  it('shows an error toast and does not deploy when encoding fails', async () => {
    mocks.emittedArgs = {
      args: [{ key: 0, name: 'owner', val: new AnyFieldValue('not valid') }],
      kwargs: {},
    };
    const wrapper = mountComponent();
    await wrapper.find('[data-testid="deploy"]').trigger('click');
    await flushPromises();

    expect(mocks.deployContract).not.toHaveBeenCalled();
    expect(mocks.notify).toHaveBeenCalledWith({
      title: 'Error',
      text: 'Invalid value for parameter "owner": Unable to consume token: not (at column 1)',
      type: 'error',
    });
    expect(wrapper.emitted('deployed-contract')).toBeUndefined();
  });

  it('deploys with the encoded arguments when input is valid', async () => {
    mocks.emittedArgs = {
      args: [{ key: 0, name: 'owner', val: new AnyFieldValue('[1, "a"]') }],
      kwargs: {},
    };
    mocks.deployContract.mockResolvedValueOnce({});
    const wrapper = mountComponent();
    await wrapper.find('[data-testid="deploy"]').trigger('click');
    await flushPromises();

    expect(mocks.notify).not.toHaveBeenCalled();
    expect(mocks.deployContract).toHaveBeenCalledWith(
      { args: [[1n, 'a']], kwargs: {} },
      'NORMAL',
      3,
    );
    expect(wrapper.emitted('deployed-contract')).toHaveLength(1);
  });
});
