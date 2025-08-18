import { defineStore } from 'pinia';
import { ref } from 'vue';
import { useRpcClient, useWebSocketClient } from '@/hooks';

// Helper function to get stored value or default
const getStoredValue = (key: string, defaultValue: number): number => {
  const stored = localStorage.getItem(`consensusStore.${key}`);
  return stored ? Number(stored) : defaultValue;
};

export const useConsensusStore = defineStore('consensusStore', () => {
  const rpcClient = useRpcClient();
  const webSocketClient = useWebSocketClient();
  const finalityWindow = ref(Number(import.meta.env.VITE_FINALITY_WINDOW));
  const isLoading = ref<boolean>(true); // Needed for the delay between creating the variable and fetching the initial value
  const leaderTimeoutFee = ref(
    getStoredValue(
      'leaderTimeoutFee',
      Number(import.meta.env.VITE_LEADER_TIMEOUT_FEE),
    ),
  );
  const validatorsTimeoutFee = ref(
    getStoredValue(
      'validatorsTimeoutFee',
      Number(import.meta.env.VITE_VALIDATORS_TIMEOUT_FEE),
    ),
  );
  const appealRoundFee = ref(
    getStoredValue(
      'appealRoundFee',
      Number(import.meta.env.VITE_APPEAL_ROUNDS_FEE),
    ),
  );
  const defaultRotationFee = Number(import.meta.env.VITE_ROTATIONS_FEE);
  const rotationsFee = ref<number[]>(
    ((): number[] => {
      try {
        const stored = localStorage.getItem('consensusStore.rotationsFee');
        if (stored) {
          return JSON.parse(stored);
        }
      } catch (error) {
        console.warn(
          'Failed to parse stored rotationsFee, using default:',
          error,
        );
      }
      // Initialize based on current appealRoundFee
      const rounds = appealRoundFee.value + 1;
      return rounds > 0 ? Array(rounds).fill(defaultRotationFee) : [];
    })(),
  );

  if (!webSocketClient.connected) webSocketClient.connect();

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

  function setLeaderTimeoutFee(fee: number) {
    leaderTimeoutFee.value = fee;
    localStorage.setItem('consensusStore.leaderTimeoutFee', fee.toString());
  }

  function setValidatorsTimeoutFee(fee: number) {
    validatorsTimeoutFee.value = fee;
    localStorage.setItem('consensusStore.validatorsTimeoutFee', fee.toString());
  }

  function setAppealRoundFee(fee: number) {
    appealRoundFee.value = fee;
    localStorage.setItem('consensusStore.appealRoundFee', fee.toString());

    // Increment fee for rotation calculations
    const rotationCount = fee + 1;
    const currentRotations = rotationsFee.value;

    // Adjust rotations fee array based on new count
    if (rotationCount > currentRotations.length) {
      // Add new rounds with default fee
      rotationsFee.value = [
        ...currentRotations,
        ...Array(rotationCount - currentRotations.length).fill(
          defaultRotationFee,
        ),
      ];
    } else if (rotationCount < currentRotations.length) {
      // Remove excess rounds
      rotationsFee.value = currentRotations.slice(0, rotationCount);
    }

    localStorage.setItem(
      'consensusStore.rotationsFee',
      JSON.stringify(rotationsFee.value),
    );
  }

  function setRotationsFee(roundIndex: number, fee: number) {
    const newRotationsFee = [...rotationsFee.value];
    newRotationsFee[roundIndex] = fee;
    rotationsFee.value = newRotationsFee;
    localStorage.setItem(
      'consensusStore.rotationsFee',
      JSON.stringify(rotationsFee.value),
    );
  }

  return {
    finalityWindow,
    setFinalityWindowTime,
    fetchFinalityWindowTime,
    isLoading,
    leaderTimeoutFee,
    setLeaderTimeoutFee,
    validatorsTimeoutFee,
    setValidatorsTimeoutFee,
    appealRoundFee,
    setAppealRoundFee,
    rotationsFee,
    setRotationsFee,
  };
});
