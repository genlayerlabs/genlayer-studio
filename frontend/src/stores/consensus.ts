import { defineStore } from 'pinia';
import { ref } from 'vue';
import { useRpcClient, useWebSocketClient } from '@/hooks';

export const useConsensusStore = defineStore('consensusStore', () => {
  const rpcClient = useRpcClient();
  const webSocketClient = useWebSocketClient();
  const finalityWindow = ref(Number(import.meta.env.VITE_FINALITY_WINDOW));
  const isLoading = ref<boolean>(true); // Needed for the delay between creating the variable and fetching the initial value
  const maxRotations = ref(Number(import.meta.env.VITE_MAX_ROTATIONS));

  type FinalityWindowPayload = {
    data: {
      time: number;
    };
  };

  // Track initialization state to prevent duplicate loading state changes
  let hasInitialized = false;

  // Get the value when the frontend or backend is reloaded
  const handleConnect = () => {
    void fetchFinalityWindowTime();
  };

  async function fetchFinalityWindowTime() {
    try {
      finalityWindow.value = await rpcClient.getFinalityWindowTime(); // Assume this RPC method exists
      if (!hasInitialized) {
        isLoading.value = false;
        hasInitialized = true;
      }
    } catch (error) {
      console.error('Failed to fetch initial finality window time: ', error);
      if (!hasInitialized) {
        isLoading.value = false;
        hasInitialized = true;
      }
    }
  }

  function setupReconnectionListener() {
    // Get the value when the backend is reloaded/reconnected
    webSocketClient.off('connect', fetchFinalityWindowTime);
    webSocketClient.on('connect', fetchFinalityWindowTime);
  }

  // Named handler for finality window time updates
  const handleFinalityWindowTimeUpdate = (eventData: any) => {
    finalityWindow.value = eventData.data.time;
  };

  // Get the value when the backend updates its value from an RPC request
  // Use off/on pattern to prevent duplicate listeners during HMR/re-inits
  webSocketClient.off(
    'finality_window_time_updated',
    handleFinalityWindowTimeUpdate,
  );
  webSocketClient.on(
    'finality_window_time_updated',
    handleFinalityWindowTimeUpdate,
  );

  // Set the value when the frontend updates its value
  async function setFinalityWindowTime(time: number) {
    await rpcClient.setFinalityWindowTime(time);
    finalityWindow.value = time;
  }

  function setMaxRotations(rotations: number) {
    maxRotations.value = rotations;
  }

  function disposeListeners() {
    webSocketClient.off('connect', handleConnect);
    webSocketClient.off(
      'finality_window_time_updated',
      handleFinalityWindowTimeUpdate,
    );
  }

  if (import.meta.hot) {
    import.meta.hot.dispose(() => {
      disposeListeners();
    });
  }

  return {
    finalityWindow,
    setFinalityWindowTime,
    fetchFinalityWindowTime,
    setupReconnectionListener,
    isLoading,
    maxRotations,
    setMaxRotations,
  };
});
