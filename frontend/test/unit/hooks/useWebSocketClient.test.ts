import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

let webSocketCtor: vi.Mock;

beforeEach(() => {
  vi.resetModules();
  webSocketCtor = vi.fn(() => ({
    send: vi.fn(),
    close: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    readyState: 1,
  }));

  Object.defineProperty(webSocketCtor, 'OPEN', {
    value: 1,
    configurable: true,
  });

  vi.stubGlobal('WebSocket', webSocketCtor);
});

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe('useWebSocketClient', () => {
  it('should create a WebSocket client with the correct URL', async () => {
    const { useWebSocketClient } = await import('@/hooks/useWebSocketClient');
    const client = useWebSocketClient();
    const expectedUrl = (client as unknown as { url: string }).url;

    webSocketCtor.mockClear();
    client.connect();

    expect(webSocketCtor).toHaveBeenCalledWith(expectedUrl);
  });

  it('should have an emit method', async () => {
    const { useWebSocketClient } = await import('@/hooks/useWebSocketClient');
    const client = useWebSocketClient();
    expect(client.emit).toBeDefined();
    expect(typeof client.emit).toBe('function');
  });

  it('should have an on method for event handling', async () => {
    const { useWebSocketClient } = await import('@/hooks/useWebSocketClient');
    const client = useWebSocketClient();
    expect(client.on).toBeDefined();
    expect(typeof client.on).toBe('function');
  });

  it('should have an off method for removing event handlers', async () => {
    const { useWebSocketClient } = await import('@/hooks/useWebSocketClient');
    const client = useWebSocketClient();
    expect(client.off).toBeDefined();
    expect(typeof client.off).toBe('function');
  });

  it('should have a connect method', async () => {
    const { useWebSocketClient } = await import('@/hooks/useWebSocketClient');
    const client = useWebSocketClient();
    expect(client.connect).toBeDefined();
    expect(typeof client.connect).toBe('function');
  });

  it('should have a disconnect method', async () => {
    const { useWebSocketClient } = await import('@/hooks/useWebSocketClient');
    const client = useWebSocketClient();
    expect(client.disconnect).toBeDefined();
    expect(typeof client.disconnect).toBe('function');
  });

  it('should handle event listeners', async () => {
    const { useWebSocketClient } = await import('@/hooks/useWebSocketClient');
    const client = useWebSocketClient();
    const mockHandler = vi.fn();

    client.on('test-event', mockHandler);
    client.off('test-event', mockHandler);

    expect(mockHandler).not.toHaveBeenCalled();
  });
});
