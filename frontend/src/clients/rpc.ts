const JSON_RPC_SERVER_URL =
  import.meta.env.VITE_JSON_RPC_SERVER_URL || 'http://localhost:4000/api';
import type { JsonRPCRequest, JsonRPCResponse } from '@/types';
import { v4 as uuidv4 } from 'uuid';
import { useWebSocketClient } from '@/hooks';

export interface IRpcClient {
  call<T>(request: JsonRPCRequest): Promise<JsonRPCResponse<T>>;
}

export class RpcClient implements IRpcClient {
  async call<T>({
    method,
    params,
  }: JsonRPCRequest): Promise<JsonRPCResponse<T>> {
    const webSocketClient = useWebSocketClient();
    // Wait for the websocket client to connect
    await new Promise<void>((resolve) => {
      if (webSocketClient.connected) {
        resolve();
      } else {
        webSocketClient.on('connect', () => {
          resolve();
        });
      }
    });

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
        'x-session-id': webSocketClient.id ?? '',
      },
      body: JSON.stringify(data),
    });

    // Check response status
    if (!response.ok) {
      const errorText = await response.text().catch(() => response.statusText);
      throw new Error(
        `RPC request failed: ${response.status} ${errorText || response.statusText}`,
      );
    }

    // Get response text first to check if it's empty
    const responseText = await response.text();
    if (!responseText || !responseText.trim()) {
      throw new Error('Empty response from RPC server');
    }

    // Parse JSON with error handling
    try {
      return JSON.parse(responseText) as JsonRPCResponse<T>;
    } catch (parseError) {
      throw new Error(
        `Failed to parse JSON response: ${parseError instanceof Error ? parseError.message : String(parseError)}. Response: ${responseText.substring(0, 200)}`,
      );
    }
  }
}
