/**
 * Native WebSocket client implementation
 * Replaces Socket.IO with standard WebSockets for FastAPI compatibility
 */

import { useNetworkStore } from '@/stores/network';

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
  private pingInterval: ReturnType<typeof setInterval> | null = null;
  private readonly PING_INTERVAL_MS: number = 15000; // 15 seconds - shorter than typical proxy timeouts
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

        // Start ping/pong heartbeat to keep connection alive
        this.startPingInterval();

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

        // Stop ping/pong heartbeat
        this.stopPingInterval();

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

  private startPingInterval() {
    this.stopPingInterval(); // Clear any existing interval
    // Send first ping immediately to establish heartbeat
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.emit('ping', { timestamp: Date.now() });
    }
    this.pingInterval = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.emit('ping', { timestamp: Date.now() });
      }
    }, this.PING_INTERVAL_MS);
  }

  private stopPingInterval() {
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
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
    this.stopPingInterval();
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
let currentUrl: string | null = null;

/**
 * A no-op stub returned when there is no WebSocket endpoint for the current
 * network (e.g. Bradbury). Callers still get the same interface so existing
 * `.on()` / `.emit()` / `.connected` usage compiles and runs without branches.
 */
const nullClient: NativeWebSocketClient = new Proxy(
  {},
  {
    get(_target, prop) {
      if (prop === 'connected') return false;
      if (prop === 'id') return null;
      if (prop === 'on' || prop === 'off' || prop === 'emit') {
        return () => undefined;
      }
      if (
        prop === 'disconnect' ||
        prop === 'reconnectNow' ||
        prop === 'connect'
      ) {
        return () => undefined;
      }
      return undefined;
    },
  },
) as unknown as NativeWebSocketClient;

export function useWebSocketClient(url?: string): NativeWebSocketClient {
  // Resolve the URL: explicit argument wins; otherwise derive from the
  // currently-selected network (non-Studio chains have no WS endpoint).
  let resolvedUrl: string | null = url ?? null;
  if (resolvedUrl === null) {
    try {
      resolvedUrl = useNetworkStore().wsUrl;
    } catch {
      // Pinia not yet initialized (very early boot) — caller gets the stub.
      return nullClient;
    }
  }

  if (!resolvedUrl) {
    // Non-Studio network or caller opted out: ensure any existing singleton
    // is torn down so we don't keep a stale WS alive after a network switch.
    if (webSocketClient) {
      disposeWebSocketClient();
    }
    return nullClient;
  }

  // If the URL changed since the singleton was created, tear it down and
  // recreate against the new endpoint.
  if (webSocketClient && currentUrl && currentUrl !== resolvedUrl) {
    disposeWebSocketClient();
  }

  // Return existing client if already created or currently initializing
  if (webSocketClient && (webSocketClient.connected || isInitializing)) {
    return webSocketClient;
  }

  // Create new client if none exists
  if (!webSocketClient && !isInitializing) {
    isInitializing = true;
    currentUrl = resolvedUrl;
    webSocketClient = new NativeWebSocketClient(resolvedUrl);

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

  return webSocketClient ?? nullClient;
}

export async function useWebSocketClientAsync(
  url?: string,
): Promise<NativeWebSocketClient> {
  const client = useWebSocketClient(url);

  if (client.connected) {
    return client;
  }

  return new Promise<NativeWebSocketClient>((resolve) => {
    client.on('connect', () => {
      resolve(client);
    });
  });
}

/**
 * Dispose the current WebSocket singleton. Safe to call repeatedly. After
 * dispose, `useWebSocketClient(url)` returns `nullClient` until the caller
 * explicitly reinitializes with a URL (or calls `ensureWebSocketClient`).
 */
export function disposeWebSocketClient() {
  if (webSocketClient) {
    webSocketClient.disconnect();
    webSocketClient = null;
  }
  isInitializing = false;
  currentUrl = null;
}

/**
 * Ensures a WebSocket is running against `url`. If the current singleton is
 * connected to a different URL, it is torn down and replaced. Pass a falsy
 * url to fully dispose the client (e.g. when switching to a non-Studio chain).
 */
export function ensureWebSocketClient(
  url: string | null,
): NativeWebSocketClient {
  if (!url) {
    disposeWebSocketClient();
    return nullClient;
  }
  if (webSocketClient && currentUrl === url) {
    return webSocketClient;
  }
  // URL changed or no client yet — tear down and recreate.
  disposeWebSocketClient();
  return useWebSocketClient(url);
}
