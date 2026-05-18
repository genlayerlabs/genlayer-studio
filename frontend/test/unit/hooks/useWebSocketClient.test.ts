import { describe, it, expect, vi } from 'vitest';

// Mock WebSocket with readyState constants
const WebSocketMock: any = vi.fn(() => ({
  send: vi.fn(),
  close: vi.fn(),
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
  readyState: 1, // OPEN
}));
Object.assign(WebSocketMock, {
  CONNECTING: 0,
  OPEN: 1,
  CLOSING: 2,
  CLOSED: 3,
});
(global as any).WebSocket = WebSocketMock;

// Reusable mock network store so `useWebSocketClient()` (no args) can resolve
// a URL without requiring a real Pinia instance in the test runtime.
const mockNetworkStore = {
  wsUrl: 'ws://localhost:4000' as string | null,
};
vi.mock('@/stores/network', () => ({
  useNetworkStore: vi.fn(() => mockNetworkStore),
}));

describe('useWebSocketClient', () => {
  it('creates a WebSocket client using the network store URL', async () => {
    const { useWebSocketClient } = await import('@/hooks/useWebSocketClient');
    WebSocketMock.mockClear();
    const client = useWebSocketClient();
    expect(client).toBeDefined();
    expect(WebSocketMock).toHaveBeenCalled();
  });

  it('returns a no-op stub when the current network has no WS url', async () => {
    mockNetworkStore.wsUrl = null;
    vi.resetModules();
    WebSocketMock.mockClear();
    const { useWebSocketClient } = await import('@/hooks/useWebSocketClient');
    const client = useWebSocketClient();
    expect(client).toBeDefined();
    expect(WebSocketMock).not.toHaveBeenCalled();
    expect(client.connected).toBe(false);
    // Restore for subsequent tests.
    mockNetworkStore.wsUrl = 'ws://localhost:4000';
  });

  it('accepts an explicit URL override', async () => {
    vi.resetModules();
    WebSocketMock.mockClear();
    const { useWebSocketClient } = await import('@/hooks/useWebSocketClient');
    const client = useWebSocketClient('ws://explicit:5000');
    expect(client).toBeDefined();
    expect(WebSocketMock).toHaveBeenCalled();
  });

  it('exposes the standard emit / on / off / disconnect surface', async () => {
    vi.resetModules();
    const { useWebSocketClient } = await import('@/hooks/useWebSocketClient');
    const client = useWebSocketClient('ws://localhost:4000');
    expect(typeof client.emit).toBe('function');
    expect(typeof client.on).toBe('function');
    expect(typeof client.off).toBe('function');
    expect(typeof client.disconnect).toBe('function');
  });

  it('handles event listeners without throwing', async () => {
    vi.resetModules();
    const { useWebSocketClient } = await import('@/hooks/useWebSocketClient');
    const client = useWebSocketClient('ws://localhost:4000');
    const mockHandler = vi.fn();

    client.on('test-event', mockHandler);
    client.off('test-event', mockHandler);

    expect(mockHandler).not.toHaveBeenCalled();
  });
});
