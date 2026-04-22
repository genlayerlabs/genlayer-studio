import { computed } from 'vue';
import { getRuntimeConfigBoolean } from '@/utils/runtimeConfig';
import { useNetworkStore } from '@/stores/network';

export const useConfig = () => {
  const isHostedEnvironment = getRuntimeConfigBoolean('VITE_IS_HOSTED', false);
  const networkStore = useNetworkStore();

  // Studio-only features: validator / provider / finality editors depend on
  // `sim_*` RPC methods that only the Studio backend exposes. Disabled in
  // hosted environments and on any non-Studio network (e.g. Bradbury).
  const isStudioNetwork = computed(() => networkStore.isStudio);
  const canUpdateValidators = computed(
    () => !isHostedEnvironment && isStudioNetwork.value,
  );
  const canUpdateProviders = computed(
    () => !isHostedEnvironment && isStudioNetwork.value,
  );
  const canUpdateFinalityWindow = computed(
    () => !isHostedEnvironment && isStudioNetwork.value,
  );

  return {
    isStudioNetwork,
    canUpdateValidators,
    canUpdateProviders,
    canUpdateFinalityWindow,
  };
};
