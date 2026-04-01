import type { JsonRPCRequest, JsonRPCResponse } from '@/types';
import { v4 as uuidv4 } from 'uuid';
import { useWebSocketClient } from '@/hooks';
import { getRuntimeConfig } from '@/utils/runtimeConfig';
import { isStudioNetwork } from '@/hooks/useConfig';

const JSON_RPC_SERVER_URL = getRuntimeConfig(
  'VITE_JSON_RPC_SERVER_URL',
  'http://127.0.0.1:4000/api',
);

export interface IRpcClient {
  call<T>(request: JsonRPCRequest): Promise<JsonRPCResponse<T>>;
}

export class RpcClient implements IRpcClient {
  async call<T>({
    method,
    params,
  }: JsonRPCRequest): Promise<JsonRPCResponse<T>> {
    let sessionId = '';

    if (isStudioNetwork()) {
      const webSocketClient = useWebSocketClient();
      // Wait for the websocket client to connect (Studio only)
      await new Promise<void>((resolve) => {
        if (webSocketClient.connected) {
          resolve();
        } else {
          webSocketClient.on('connect', () => {
            resolve();
          });
        }
      });
      sessionId = webSocketClient.id ?? '';
    }

    const requestId = uuidv4();
    const data = {
      jsonrpc: '2.0',
      method,
      params,
      id: requestId,
    };
    const response = await fetch(JSON_RPC_SERVER_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(sessionId ? { 'x-session-id': sessionId } : {}),
      },
      body: JSON.stringify(data),
    });
    return response.json() as Promise<JsonRPCResponse<T>>;
  }
}
