import type { JsonRPCRequest, JsonRPCResponse } from '@/types';
import { v4 as uuidv4 } from 'uuid';
import { useWebSocketClient } from '@/hooks';
import { useNetworkStore } from '@/stores/network';

export interface IRpcClient {
  call<T>(request: JsonRPCRequest): Promise<JsonRPCResponse<T>>;
}

export class RpcClient implements IRpcClient {
  async call<T>({
    method,
    params,
  }: JsonRPCRequest): Promise<JsonRPCResponse<T>> {
    const networkStore = useNetworkStore();
    const endpoint = networkStore.rpcUrl;
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };

    // WebSocket session ID is only meaningful on Studio (no WS on testnets).
    if (networkStore.isStudio) {
      const webSocketClient = useWebSocketClient();
      await new Promise<void>((resolve) => {
        if (webSocketClient.connected) {
          resolve();
        } else {
          webSocketClient.on('connect', () => {
            resolve();
          });
        }
      });
      headers['x-session-id'] = webSocketClient.id ?? '';
    }

    const requestId = uuidv4();
    const data = {
      jsonrpc: '2.0',
      method,
      params,
      id: requestId,
    };
    const response = await fetch(endpoint, {
      method: 'POST',
      headers,
      body: JSON.stringify(data),
    });
    return response.json() as Promise<JsonRPCResponse<T>>;
  }
}
