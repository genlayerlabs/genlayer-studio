import { getRuntimeConfigBoolean } from '@/utils/runtimeConfig';

export const useConfig = () => {
  const isHostedEnvironment = getRuntimeConfigBoolean('VITE_IS_HOSTED', false);
  const canUpdateValidators = !isHostedEnvironment;
  const canUpdateProviders = !isHostedEnvironment;
  const canUpdateFinalityWindow = !isHostedEnvironment;

  return {
    canUpdateValidators,
    canUpdateProviders,
    canUpdateFinalityWindow,
  };
};
