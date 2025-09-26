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
  let listenersRegistered = false;

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

  // Get the value when the backend updates its value from an RPC request
  const handleFinalityWindowUpdate = (eventData: FinalityWindowPayload) => {
    if (eventData?.data?.time !== undefined) {
      finalityWindow.value = eventData.data.time;
    }
  };
  if (!listenersRegistered) {
    webSocketClient.on('connect', handleConnect);
    webSocketClient.on(
      'finality_window_time_updated',
      handleFinalityWindowUpdate,
    );
    listenersRegistered = true;
  }

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
      handleFinalityWindowUpdate,
    );
    listenersRegistered = false;
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
    isLoading,
    maxRotations,
    setMaxRotations,
  };
});
