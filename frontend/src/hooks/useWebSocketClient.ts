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
    console.log('=== WEBSOCKET CONSTRUCTOR ===');
    console.log('Input URL:', url);

    // Convert HTTP URL to WebSocket URL
    this.url = url.replace('http://', 'ws://').replace('https://', 'wss://');
    console.log('After protocol conversion:', this.url);

    // Append /ws endpoint for FastAPI
    if (!this.url.endsWith('/ws')) {
      this.url = this.url.replace(/\/$/, '') + '/ws';
    }
    console.log('Final WebSocket URL:', this.url);

    this.connect();
  }

  public connect() {
    console.log('=== WEBSOCKET CONNECT ATTEMPT ===');
    console.log('Attempting to connect to:', this.url);
    console.log('Timestamp:', new Date().toISOString());

    // Test multiple connectivity approaches
    if (this.url.startsWith('wss://') || this.url.startsWith('ws://')) {
      const baseUrl = this.url
        .replace('wss://', 'https://')
        .replace('ws://', 'http://')
        .replace('/ws', '');

      // Test 1: Health endpoint
      const healthUrl = baseUrl + '/health';
      console.log('=== CONNECTIVITY TEST 1: Health Check ===');
      console.log('Testing HTTP connectivity to:', healthUrl);
      fetch(healthUrl)
        .then((response) => {
          console.log(
            'Health check SUCCESS:',
            response.status,
            response.statusText,
          );
          return response.text();
        })
        .then((body) => console.log('Health response body:', body))
        .catch((error) => console.error('Health check FAILED:', error));

      // Test 2: API endpoint
      const apiUrl = baseUrl + '/api';
      console.log('=== CONNECTIVITY TEST 2: API Check ===');
      console.log('Testing API connectivity to:', apiUrl);
      fetch(apiUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{"method":"ping","id":1}',
      })
        .then((response) => {
          console.log(
            'API check response:',
            response.status,
            response.statusText,
          );
          return response.text();
        })
        .then((body) => console.log('API response body:', body))
        .catch((error) => console.error('API check FAILED:', error));

      // Test 3: Check for proxy/CDN headers
      console.log('=== CONNECTIVITY TEST 3: Proxy Detection ===');
      fetch(healthUrl, { method: 'HEAD' })
        .then((response) => {
          console.log('Response headers:');
          for (const [key, value] of response.headers.entries()) {
            console.log(`  ${key}: ${value}`);
          }
        })
        .catch((error) => console.error('Header check failed:', error));
    }

    try {
      this.ws = new WebSocket(this.url);
      console.log(
        'WebSocket object created, initial readyState:',
        this.ws.readyState,
      );

      this.ws.onopen = () => {
        console.log('=== WEBSOCKET CONNECTED SUCCESSFULLY ===');
        console.log('Connection timestamp:', new Date().toISOString());
        console.log('Final readyState:', this.ws?.readyState);
        console.log('WebSocket URL used:', this.ws?.url);
        console.log('WebSocket protocol:', this.ws?.protocol);
        console.log('WebSocket extensions:', this.ws?.extensions);

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
        console.error('=== WEBSOCKET ERROR ===');
        console.error('Error event:', error);
        console.log('WebSocket readyState on error:', this.ws?.readyState);
        console.log('Current URL:', this.url);
      };

      this.ws.onclose = (event) => {
        console.log('=== WEBSOCKET DISCONNECTED ===');
        console.log('Close event details:', {
          code: event.code,
          reason: event.reason,
          wasClean: event.wasClean,
          readyState: (event.target as WebSocket)?.readyState,
        });
        console.log('Connection URL was:', this.url);

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
    this.reconnectAttempts++;
    const timeout = Math.min(
      this.reconnectTimeout * Math.pow(2, this.reconnectAttempts - 1),
      this.maxReconnectTimeout,
    );

    console.log('=== WEBSOCKET RECONNECTION ===');
    console.log(`Attempt: ${this.reconnectAttempts}`);
    console.log(`Timeout: ${timeout}ms`);
    console.log(`Last URL: ${this.url}`);
    console.log(`Should reconnect: ${this.shouldReconnect}`);

    setTimeout(() => {
      if (this.shouldReconnect) {
        console.log(
          `=== EXECUTING RECONNECT ATTEMPT ${this.reconnectAttempts} ===`,
        );
        this.connect();
      } else {
        console.log('=== RECONNECT CANCELLED ===');
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
          console.error(`Error in event handler for ${event}:`, error);
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

// Module startup logging
console.log('=== useWebSocketClient.ts MODULE LOADED ===');
console.log('Module load timestamp:', new Date().toISOString());
console.log('Document readyState:', document.readyState);
console.log('Window location:', window.location.href);

// Singleton instance with initialization state tracking
let webSocketClient: NativeWebSocketClient | null = null;
let isInitializing: boolean = false;

export function useWebSocketClient(): NativeWebSocketClient {
  console.log('=== useWebSocketClient() called ===');
  console.log('Existing client:', !!webSocketClient);
  console.log('Client connected:', webSocketClient?.connected);
  console.log('Is initializing:', isInitializing);
  console.log('Call stack:', new Error().stack);

  // Return existing client if already created or currently initializing
  if (webSocketClient && (webSocketClient.connected || isInitializing)) {
    console.log('=== Returning existing WebSocket client ===');
    return webSocketClient;
  }

  // Create new client if none exists
  if (!webSocketClient && !isInitializing) {
    console.log('=== Creating new WebSocket client ===');
    isInitializing = true;
    const wsUrl = import.meta.env.VITE_WS_SERVER_URL || 'ws://localhost:4000';

    // PRODUCTION DEBUG: Log environment and URL construction
    console.log('=== WEBSOCKET CLIENT INIT ===');
    console.log('Environment:', {
      NODE_ENV: import.meta.env.NODE_ENV,
      MODE: import.meta.env.MODE,
      PROD: import.meta.env.PROD,
      DEV: import.meta.env.DEV,
      VITE_WS_SERVER_URL: import.meta.env.VITE_WS_SERVER_URL,
      location: window.location.href,
      userAgent: navigator.userAgent,
      buildTime: import.meta.env.VITE_BUILD_TIME || 'UNKNOWN',
      allEnvVars: import.meta.env,
    });
    console.log('Final WebSocket URL will be:', wsUrl);

    // Check network and browser capabilities
    console.log('=== BROWSER/NETWORK INFO ===');
    console.log('WebSocket support:', typeof WebSocket !== 'undefined');
    console.log(
      'Connection type:',
      (navigator as any).connection?.effectiveType || 'UNKNOWN',
    );
    console.log('Online status:', navigator.onLine);

    // Check if any old Socket.IO code is still running
    if ((window as any).io) {
      console.warn('=== WARNING: Socket.IO client detected on window.io ===');
    }
    if (document.querySelector('script[src*="socket.io"]')) {
      console.warn('=== WARNING: Socket.IO script tag found in document ===');
    }

    // Check for any other WebSocket clients
    const scripts = Array.from(document.querySelectorAll('script[src]'));
    const wsRelatedScripts = scripts.filter(
      (s) =>
        s.getAttribute('src')?.includes('socket') ||
        s.getAttribute('src')?.includes('ws') ||
        s.getAttribute('src')?.includes('websocket'),
    );
    if (wsRelatedScripts.length > 0) {
      console.log('=== WebSocket-related scripts found ===');
      wsRelatedScripts.forEach((s) =>
        console.log('  -', s.getAttribute('src')),
      );
    }

    // Check for Service Workers or other interceptors
    console.log('=== SERVICE WORKER & INTERCEPTOR CHECK ===');
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.getRegistrations().then((registrations) => {
        console.log('Service Worker registrations:', registrations.length);
        registrations.forEach((reg) => console.log('  - SW scope:', reg.scope));
      });
    }

    // Check for any global WebSocket overrides
    console.log('WebSocket constructor:', WebSocket);
    console.log(
      'WebSocket prototype:',
      Object.getOwnPropertyNames(WebSocket.prototype),
    );

    // Check if fetch has been overridden (could affect our connectivity tests)
    console.log('fetch function:', fetch.toString().substring(0, 100) + '...');

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
