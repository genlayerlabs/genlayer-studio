import { defineStore } from 'pinia';
import { ref } from 'vue';
import { useRpcClient, useWebSocketClient } from '@/hooks';

export const useConsensusStore = defineStore('consensusStore', () => {
  const rpcClient = useRpcClient();
  const webSocketClient = useWebSocketClient();
  const finalityWindow = ref(Number(import.meta.env.VITE_FINALITY_WINDOW));
  const isLoading = ref<boolean>(true); // Needed for the delay between creating the variable and fetching the initial value
  const maxRotations = ref(Number(import.meta.env.VITE_MAX_ROTATIONS));

  // Get the value when the frontend or backend is reloaded
  webSocketClient.on('connect', fetchFinalityWindowTime);

  async function fetchFinalityWindowTime() {
    try {
      finalityWindow.value = await rpcClient.getFinalityWindowTime(); // Assume this RPC method exists
    } catch (error) {
      console.error('Failed to fetch initial finality window time: ', error);
    } finally {
      isLoading.value = false;
    }
  }

  // Get the value when the backend updates its value from an RPC request
  webSocketClient.on('finality_window_time_updated', (eventData: any) => {
    finalityWindow.value = eventData.data.time;
  });

  // Set the value when the frontend updates its value
  async function setFinalityWindowTime(time: number) {
    await rpcClient.setFinalityWindowTime(time);
    finalityWindow.value = time;
  }

  function setMaxRotations(rotations: number) {
    maxRotations.value = rotations;
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
