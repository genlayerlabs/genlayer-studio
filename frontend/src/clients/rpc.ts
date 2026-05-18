import type { JsonRPCRequest, JsonRPCResponse } from '@/types';
import { useWebSocketClient } from '@/hooks';
import { useNetworkStore } from '@/stores/network';

export interface IRpcClient {
  call<T>(request: JsonRPCRequest): Promise<JsonRPCResponse<T>>;
}

// JSON-RPC 2.0 allows string or number ids, but the Go node we talk to on
// Bradbury/Asimov rejects strings (`cannot unmarshal string into Go struct
// field Request.id of type int`). Use a monotonic counter to stay compatible
// with both the Studio Python backend and Go testnet nodes.
let nextRequestId = 1;

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

    const data = {
      jsonrpc: '2.0',
      method,
      params,
      id: nextRequestId++,
    };
    const response = await fetch(endpoint, {
      method: 'POST',
      headers,
      body: JSON.stringify(data),
    });
    return response.json() as Promise<JsonRPCResponse<T>>;
  }
}
