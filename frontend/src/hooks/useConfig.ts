import {
  getRuntimeConfig,
  getRuntimeConfigBoolean,
} from '@/utils/runtimeConfig';

/**
 * Whether the current environment connects to a Studio backend
 * (has WebSocket, supports sim_* RPCs, validator/provider management).
 */
export function isStudioNetwork(): boolean {
  return !!getRuntimeConfig('VITE_WS_SERVER_URL', '');
}

export const useConfig = () => {
  const isHostedEnvironment = getRuntimeConfigBoolean('VITE_IS_HOSTED', false);
  const isStudio = isStudioNetwork();

  // Validator/provider management only available on non-hosted Studio
  const canUpdateValidators = isStudio && !isHostedEnvironment;
  const canUpdateProviders = isStudio && !isHostedEnvironment;
  const canUpdateFinalityWindow = isStudio && !isHostedEnvironment;

  // Studio-only features (available on any Studio, even hosted)
  const canUpgradeContracts = isStudio;
  const canCancelTransactions = isStudio;
  const canLintContracts = isStudio;
  const hasNodeLogs = isStudio;
  const hasValidatorsPanel = isStudio;

  return {
    isStudio,
    canUpdateValidators,
    canUpdateProviders,
    canUpdateFinalityWindow,
    canUpgradeContracts,
    canCancelTransactions,
    canLintContracts,
    hasNodeLogs,
    hasValidatorsPanel,
  };
};
