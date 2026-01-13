import { defineStore } from 'pinia';
import { ref } from 'vue';
import { useWebSocketClient } from '@/hooks';

export const useConnectionStatusStore = defineStore('connectionStatus', () => {
  const webSocketClient = useWebSocketClient();
  const isConnected = ref(webSocketClient.connected);

  // Named handlers for connection events
  const handleConnect = () => {
    isConnected.value = true;
  };

  const handleDisconnect = () => {
    isConnected.value = false;
  };

  // Use off/on pattern to prevent duplicate listeners during HMR/re-inits
  webSocketClient.off('connect', handleConnect);
  webSocketClient.on('connect', handleConnect);

  webSocketClient.off('disconnect', handleDisconnect);
  webSocketClient.on('disconnect', handleDisconnect);

  // Cleanup for HMR
  if (import.meta.hot) {
    import.meta.hot.dispose(() => {
      webSocketClient.off('connect', handleConnect);
      webSocketClient.off('disconnect', handleDisconnect);
    });
  }

  return {
    isConnected,
  };
});
