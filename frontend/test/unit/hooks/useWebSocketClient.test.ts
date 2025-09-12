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
    mockOn.mockClear();
    (io as any).mockClear();
  });

  it('should create a WebSocket client with the correct URL', () => {
    useWebSocketClient();
    expect(io).toHaveBeenCalledWith(import.meta.env.VITE_WS_SERVER_URL);
  });

  it('should return a socket client instance', () => {
    const client = useWebSocketClient();

    expect(client).toHaveProperty('id', 'mocked-socket-id');
    expect(client).toHaveProperty('on');
  });

  it('should reuse the existing WebSocket client on subsequent calls', () => {
    const client1 = useWebSocketClient();
    const client2 = useWebSocketClient();
    expect(client1).toBe(client2);
  });
});
