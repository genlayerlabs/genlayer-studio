import { defineStore } from 'pinia';
import { ref } from 'vue';
import { useWebSocketClient } from '@/hooks';

export const useConnectionStatusStore = defineStore('connectionStatus', () => {
  const webSocketClient = useWebSocketClient();
  // Start as true to avoid showing banner during initial connection
  // Only show "connection lost" after we've connected at least once
  const isConnected = ref(true);
  let hasConnectedOnce = false;
  let disconnectTimer: ReturnType<typeof setTimeout> | null = null;

  // Debounce delay before showing connection lost banner (in ms)
  const DISCONNECT_DEBOUNCE_MS = 5000;

  // Named handlers for connection events
  const handleConnect = () => {
    hasConnectedOnce = true;
    isConnected.value = true;

    // Clear any pending disconnect timer
    if (disconnectTimer) {
      clearTimeout(disconnectTimer);
      disconnectTimer = null;
    }
  };

  const handleDisconnect = () => {
    // Only show disconnected state if we've successfully connected before
    if (hasConnectedOnce) {
      // Debounce: wait before showing banner to allow reconnection attempts
      if (!disconnectTimer) {
        disconnectTimer = setTimeout(() => {
          isConnected.value = false;
          disconnectTimer = null;
        }, DISCONNECT_DEBOUNCE_MS);
      }
    }
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
      if (disconnectTimer) {
        clearTimeout(disconnectTimer);
        disconnectTimer = null;
      }
    });
  }

  return {
    isConnected,
  };
});
