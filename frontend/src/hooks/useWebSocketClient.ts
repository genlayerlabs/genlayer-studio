/**
 * Native WebSocket client implementation
 * Replaces Socket.IO with standard WebSockets for FastAPI compatibility
 */

interface WebSocketMessage {
  event: string;
  data?: any;
}

class NativeWebSocketClient {
  private ws: WebSocket | null = null;
  private url: string;
  private eventHandlers: Map<string, Set<Function>> = new Map();
  private reconnectTimeout: number = 1000;
  private maxReconnectTimeout: number = 30000;
  private reconnectAttempts: number = 0;
  private shouldReconnect: boolean = true;
  private subscribedTopics: Set<string> = new Set();
  public id: string | null = null;
  public connected: boolean = false;

  constructor(url: string) {
    // Convert HTTP URL to WebSocket URL
    this.url = url.replace('http://', 'ws://').replace('https://', 'wss://');
    // Append /ws endpoint for FastAPI
    if (!this.url.endsWith('/ws')) {
      this.url = this.url.replace(/\/$/, '') + '/ws';
    }
    this.connect();
  }

  public connect() {
    try {
      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => {
        this.id = `ws-${Date.now()}`;
        this.connected = true;
        this.reconnectAttempts = 0;
        this.reconnectTimeout = 1000;

        // Trigger connect event
        this.triggerEvent('connect', null);

        // Re-subscribe to topics after reconnection
        if (this.subscribedTopics.size > 0) {
          this.emit('subscribe', Array.from(this.subscribedTopics));
        }
      };

      this.ws.onmessage = (event) => {
        try {
          const message: WebSocketMessage = JSON.parse(event.data);
          this.handleMessage(message);
        } catch (error) {
          console.error('Failed to parse WebSocket message:', error);
        }
      };

      this.ws.onerror = (error) => {
        console.error('WebSocket error:', error);
      };

      this.ws.onclose = () => {
        this.id = null;
        this.connected = false;
        this.triggerEvent('disconnect', null);

        if (this.shouldReconnect) {
          this.reconnect();
        }
      };
    } catch (error) {
      console.error('Failed to create WebSocket connection:', error);
      this.reconnect();
    }
  }

  private reconnect() {
    if (!this.shouldReconnect) {
      return;
    }

    this.reconnectAttempts++;
    const timeout = Math.min(
      this.reconnectTimeout * Math.pow(2, this.reconnectAttempts - 1),
      this.maxReconnectTimeout,
    );

    setTimeout(() => {
      if (this.shouldReconnect) {
        this.connect();
      }
    }, timeout);
  }

  private handleMessage(message: WebSocketMessage) {
    const { event, data } = message;

    // Handle internal events
    if (event === 'subscribed') {
      if (data?.room) {
        this.subscribedTopics.add(data.room);
      }
    } else if (event === 'unsubscribed') {
      if (data?.room) {
        this.subscribedTopics.delete(data.room);
      }
    }

    // Trigger event handlers
    this.triggerEvent(event, data);
  }

  private triggerEvent(event: string, data: any) {
    const handlers = this.eventHandlers.get(event);
    if (handlers) {
      handlers.forEach((handler) => {
        try {
          handler(data);
        } catch (error) {
          console.error('Error in event handler for %s:', event, error);
        }
      });
    }
  }

  public on(event: string, handler: Function) {
    if (!this.eventHandlers.has(event)) {
      this.eventHandlers.set(event, new Set());
    }
    this.eventHandlers.get(event)!.add(handler);
  }

  public off(event: string, handler: Function) {
    const handlers = this.eventHandlers.get(event);
    if (handlers) {
      handlers.delete(handler);
      if (handlers.size === 0) {
        this.eventHandlers.delete(event);
      }
    }
  }

  public emit(event: string, data?: any) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      const message: WebSocketMessage = { event, data };
      this.ws.send(JSON.stringify(message));

      // Track subscriptions locally
      if (event === 'subscribe' && Array.isArray(data)) {
        data.forEach((topic) => this.subscribedTopics.add(topic));
      } else if (event === 'unsubscribe' && Array.isArray(data)) {
        data.forEach((topic) => this.subscribedTopics.delete(topic));
      }
    } else {
      console.warn(
        'WebSocket is not connected. Message not sent:',
        event,
        data,
      );
    }
  }

  public disconnect() {
    this.shouldReconnect = false;
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  public reconnectNow() {
    this.shouldReconnect = true;
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      this.connect();
    }
  }
}

// Singleton instance with initialization state tracking
let webSocketClient: NativeWebSocketClient | null = null;
let isInitializing: boolean = false;

export function useWebSocketClient(): NativeWebSocketClient {
  // Return existing client if already created or currently initializing
  if (webSocketClient && (webSocketClient.connected || isInitializing)) {
    return webSocketClient;
  }

  // Create new client if none exists
  if (!webSocketClient && !isInitializing) {
    isInitializing = true;
    const wsUrl = import.meta.env.VITE_WS_SERVER_URL || 'ws://localhost:4000';
    webSocketClient = new NativeWebSocketClient(wsUrl);

    // Reset initialization flag when connection completes (success or failure)
    webSocketClient.on('connect', () => {
      isInitializing = false;
    });

    // Also handle connection failures
    webSocketClient.on('disconnect', () => {
      if (isInitializing) {
        isInitializing = false;
      }
    });
  }

  return webSocketClient!;
}

export async function useWebSocketClientAsync(): Promise<NativeWebSocketClient> {
  const client = useWebSocketClient();

  if (client.connected) {
    return client;
  }

  return new Promise<NativeWebSocketClient>((resolve) => {
    client.on('connect', () => {
      resolve(client);
    });
  });
}
