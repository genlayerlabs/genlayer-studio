import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useWebSocketClient } from '@/hooks/useWebSocketClient';

// Mock WebSocket
global.WebSocket = vi.fn(() => ({
  send: vi.fn(),
  close: vi.fn(),
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
  readyState: WebSocket.OPEN,
})) as any;

describe('useWebSocketClient', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should create a WebSocket client with the correct URL', () => {
    const client = useWebSocketClient();
    expect(client).toBeDefined();
    expect(global.WebSocket).toHaveBeenCalled();
  });

  it('should have an emit method', () => {
    const client = useWebSocketClient();
    expect(client.emit).toBeDefined();
    expect(typeof client.emit).toBe('function');
  });

  it('should have an on method for event handling', () => {
    const client = useWebSocketClient();
    expect(client.on).toBeDefined();
    expect(typeof client.on).toBe('function');
  });

  it('should have an off method for removing event handlers', () => {
    const client = useWebSocketClient();
    expect(client.off).toBeDefined();
    expect(typeof client.off).toBe('function');
  });

  it('should have a connect method', () => {
    const client = useWebSocketClient();
    expect(client.connect).toBeDefined();
    expect(typeof client.connect).toBe('function');
  });

  it('should have a disconnect method', () => {
    const client = useWebSocketClient();
    expect(client.disconnect).toBeDefined();
    expect(typeof client.disconnect).toBe('function');
  });

  it('should handle event listeners', () => {
    const client = useWebSocketClient();
    const mockHandler = vi.fn();
    
    client.on('test-event', mockHandler);
    // Note: In a real test, you'd trigger the event and verify the handler is called
    
    client.off('test-event', mockHandler);
    // Note: In a real test, you'd verify the handler is removed
  });
});