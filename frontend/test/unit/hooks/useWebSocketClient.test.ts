import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useWebSocketClient } from '@/hooks/useWebSocketClient';
import { io } from 'socket.io-client';

const mockOn = vi.fn();

vi.mock('socket.io-client', () => ({
  io: vi.fn(() => ({
    id: 'mocked-socket-id',
    on: mockOn,
  })),
}));

describe('useWebSocketClient', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should create a WebSocket client with the correct URL', () => {
    useWebSocketClient();
    expect(io).toHaveBeenCalledWith(import.meta.env.VITE_WS_SERVER_URL);
  });

  it('should set up connect and disconnect event handlers', () => {
    const consoleLogSpy = vi.spyOn(console, 'log').mockImplementation(() => {});

    useWebSocketClient();

    expect(mockOn).toHaveBeenCalledWith('connect', expect.any(Function));
    expect(mockOn).toHaveBeenCalledWith('disconnect', expect.any(Function));

    const connectCallback = mockOn.mock.calls.find(
      (call) => call[0] === 'connect',
    )?.[1];
    if (connectCallback) {
      connectCallback();
    }
    expect(consoleLogSpy).toHaveBeenCalledWith(
      'webSocketClient.connect',
      'mocked-socket-id',
    );

    const disconnectCallback = mockOn.mock.calls.find(
      (call) => call[0] === 'disconnect',
    )?.[1];
    if (disconnectCallback) {
      disconnectCallback();
    }
    expect(consoleLogSpy).toHaveBeenCalledWith(
      'webSocketClient.disconnnect',
      'mocked-socket-id',
    );

    consoleLogSpy.mockRestore();
  });

  it('should reuse the existing WebSocket client on subsequent calls', () => {
    const client1 = useWebSocketClient();
    const client2 = useWebSocketClient();
    expect(client1).toBe(client2);
    expect(mockOn).toHaveBeenCalledTimes(4);
  });
});
