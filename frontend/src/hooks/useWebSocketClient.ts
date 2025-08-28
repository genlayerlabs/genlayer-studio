import { io, type Socket } from 'socket.io-client';

let webSocketClient: Socket | null = null;

export function useWebSocketClient() {
  if (!webSocketClient) {
    webSocketClient = io(import.meta.env.VITE_WS_SERVER_URL);
  }

  return webSocketClient;
}
