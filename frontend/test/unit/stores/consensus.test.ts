import { describe, it, expect, beforeEach, vi } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import { useConsensusStore } from '@/stores';

let mockWebSocketClientGlobal: any = {
  connected: true,
  connect: vi.fn(),
  emit: vi.fn(),
  on: vi.fn(),
  off: vi.fn(),
};

let mockRpcClientGlobal: any = {
  getFinalityWindowTime: vi.fn().mockResolvedValue(30),
  setFinalityWindowTime: vi.fn().mockResolvedValue(undefined),
};

vi.mock('@/hooks', () => ({
  useWebSocketClient: vi.fn(() => mockWebSocketClientGlobal),
  useRpcClient: vi.fn(() => mockRpcClientGlobal),
}));

describe('useConsensusStore', () => {
  let consensusStore: ReturnType<typeof useConsensusStore>;
  let mockWebSocketClient: any;
  let mockRpcClient: any;

  beforeEach(() => {
    // Set up mocks BEFORE creating the store
    mockWebSocketClient = {
      connected: true,
      connect: vi.fn(),
      emit: vi.fn(),
      on: vi.fn(),
      off: vi.fn(),
    };
    mockWebSocketClientGlobal = mockWebSocketClient;

    mockRpcClient = {
      getFinalityWindowTime: vi.fn().mockResolvedValue(30),
      setFinalityWindowTime: vi.fn().mockResolvedValue(undefined),
    };
    mockRpcClientGlobal = mockRpcClient;

    setActivePinia(createPinia());

    // Clear mocks before creating store
    mockWebSocketClient.emit.mockClear();
    mockWebSocketClient.on.mockClear();
    mockWebSocketClient.off.mockClear();
    mockWebSocketClient.connect.mockClear();
    mockRpcClient.getFinalityWindowTime.mockClear();

    // Now create the store - this will trigger the WebSocket setup
    consensusStore = useConsensusStore();
  });

  it('should initialize with default values from environment variables', () => {
    // Since import.meta.env.VITE_FINALITY_WINDOW will be undefined in tests, expect NaN
    expect(consensusStore.finalityWindow).toBeNaN();
    expect(consensusStore.isLoading).toBe(true); // Loading starts as true
    expect(consensusStore.maxRotations).toBeNaN(); // Also NaN from undefined env var
  });

  it('should connect WebSocket if not connected', () => {
    // Update the global mock to have connected = false
    mockWebSocketClientGlobal.connected = false;
    mockWebSocketClientGlobal.connect.mockClear();

    // Create a new pinia instance and re-initialize store to trigger connection logic
    setActivePinia(createPinia());
    const newConsensusStore = useConsensusStore();

    expect(mockWebSocketClientGlobal.connect).toHaveBeenCalled();
  });

  it('should not connect WebSocket if already connected', () => {
    mockWebSocketClient.connected = true;
    mockWebSocketClient.connect.mockClear();

    // Re-initialize store
    consensusStore = useConsensusStore();

    expect(mockWebSocketClient.connect).not.toHaveBeenCalled();
  });

  describe('fetchFinalityWindowTime', () => {
    it('should fetch finality window time successfully', async () => {
      mockRpcClient.getFinalityWindowTime.mockResolvedValueOnce(45);
      consensusStore.isLoading = false; // Reset loading state

      await consensusStore.fetchFinalityWindowTime();

      expect(mockRpcClient.getFinalityWindowTime).toHaveBeenCalled();
      expect(consensusStore.finalityWindow).toBe(45);
      expect(consensusStore.isLoading).toBe(false);
    });

    it('should handle fetch error gracefully', async () => {
      const consoleErrorSpy = vi
        .spyOn(console, 'error')
        .mockImplementation(() => {});
      mockRpcClient.getFinalityWindowTime.mockRejectedValueOnce(
        new Error('Network error'),
      );
      consensusStore.isLoading = false; // Reset loading state

      await consensusStore.fetchFinalityWindowTime();

      expect(mockRpcClient.getFinalityWindowTime).toHaveBeenCalled();
      expect(consensusStore.isLoading).toBe(false);
      expect(consoleErrorSpy).toHaveBeenCalledWith(
        'Failed to fetch initial finality window time: ',
        expect.any(Error),
      );

      consoleErrorSpy.mockRestore();
    });

    it('should set loading state during fetch', async () => {
      let resolvePromise: (value: number) => void;
      const promise = new Promise<number>((resolve) => {
        resolvePromise = resolve;
      });
      mockRpcClient.getFinalityWindowTime.mockReturnValueOnce(promise);

      // Start the fetch
      const fetchPromise = consensusStore.fetchFinalityWindowTime();

      // Check loading state is true during fetch
      expect(consensusStore.isLoading).toBe(true);

      // Resolve the promise
      resolvePromise!(60);
      await fetchPromise;

      // Check loading state is false after fetch
      expect(consensusStore.isLoading).toBe(false);
      expect(consensusStore.finalityWindow).toBe(60);
    });
  });

  describe('setFinalityWindowTime', () => {
    it('should set finality window time', async () => {
      await consensusStore.setFinalityWindowTime(25);
      expect(mockRpcClient.setFinalityWindowTime).toHaveBeenCalledWith(25);
      expect(consensusStore.finalityWindow).toBe(25);
    });
  });

  describe('setMaxRotations', () => {
    it('should set max rotations', () => {
      consensusStore.setMaxRotations(5);
      expect(consensusStore.maxRotations).toBe(5);
    });
  });

  describe('WebSocket event handlers', () => {
    it('should set up finality_window_time_updated event handler', () => {
      expect(mockWebSocketClient.on).toHaveBeenCalledWith(
        'finality_window_time_updated',
        expect.any(Function),
      );
    });

    it('should update finality window time when receiving finality_window_time_updated event', () => {
      const eventHandler = mockWebSocketClient.on.mock.calls.find(
        (call: any[]) => call[0] === 'finality_window_time_updated',
      )?.[1];

      expect(eventHandler).toBeDefined();

      if (eventHandler) {
        eventHandler({ data: { time: 75 } });
      }

      expect(consensusStore.finalityWindow).toBe(75);
    });

    it('should only set up finality_window_time_updated event handler (max_rotations_updated not implemented)', () => {
      const onCalls = mockWebSocketClient.on.mock.calls;
      const eventTypes = onCalls.map((call: any[]) => call[0]);

      expect(eventTypes).toContain('finality_window_time_updated');
      expect(eventTypes).not.toContain('max_rotations_updated');
    });
  });

  describe('setupReconnectionListener', () => {
    it('should set up connect event handler when setupReconnectionListener is called', () => {
      mockWebSocketClient.on.mockClear();

      consensusStore.setupReconnectionListener();

      expect(mockWebSocketClient.on).toHaveBeenCalledWith(
        'connect',
        expect.any(Function),
      );
    });

    it('should fetch finality window time when WebSocket reconnects', async () => {
      mockRpcClient.getFinalityWindowTime.mockResolvedValueOnce(90);

      // Set up the reconnection listener
      consensusStore.setupReconnectionListener();

      // Get the connect handler that was registered
      const connectHandler = mockWebSocketClient.on.mock.calls.find(
        (call: any[]) => call[0] === 'connect',
      )?.[1];

      expect(connectHandler).toBeDefined();

      if (connectHandler) {
        await connectHandler();
      }

      expect(mockRpcClient.getFinalityWindowTime).toHaveBeenCalled();
      expect(consensusStore.finalityWindow).toBe(90);
    });

    it('should handle reconnection fetch error gracefully', async () => {
      const consoleErrorSpy = vi
        .spyOn(console, 'error')
        .mockImplementation(() => {});
      mockRpcClient.getFinalityWindowTime.mockRejectedValueOnce(
        new Error('Reconnection fetch failed'),
      );

      // Set up the reconnection listener
      consensusStore.setupReconnectionListener();

      // Get and execute the connect handler that was just added by setupReconnectionListener
      const connectHandler = mockWebSocketClient.on.mock.calls
        .filter((call: any[]) => call[0] === 'connect')
        .pop()?.[1]; // Get the most recent connect handler

      if (connectHandler) {
        await connectHandler();
      }

      expect(mockRpcClient.getFinalityWindowTime).toHaveBeenCalled();
      expect(consoleErrorSpy).toHaveBeenCalledWith(
        'Failed to fetch initial finality window time: ',
        expect.any(Error),
      );

      consoleErrorSpy.mockRestore();
    });
  });
});
