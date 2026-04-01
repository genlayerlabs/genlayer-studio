/**
 * Behavioral snapshot tests for RpcClient.
 *
 * Documents the WS-blocking behavior and HTTP call pattern.
 * The multi-network refactor must:
 * - Preserve this behavior when isStudio=true (WS exists)
 * - Skip WS wait when isStudio=false (no WS on testnet)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockFetch = vi.fn();
const mockWsClient = {
  connected: false,
  id: 'test-session-id',
  on: vi.fn(),
};

vi.mock('@/hooks', () => ({
  useWebSocketClient: vi.fn(() => mockWsClient),
}));

vi.mock('@/utils/runtimeConfig', () => ({
  getRuntimeConfig: vi.fn(() => 'http://localhost:4000/api'),
}));

// Mock uuid
vi.mock('uuid', () => ({
  v4: vi.fn(() => 'test-uuid'),
}));

global.fetch = mockFetch as any;

describe('RpcClient — behavioral contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockWsClient.connected = false;
    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ result: 'ok', error: null }),
    });
  });

  it('should block on WS connect before making HTTP call', async () => {
    vi.resetModules();
    const { RpcClient } = await import('@/clients/rpc');
    const client = new RpcClient();

    // Start the call — should be blocked waiting for WS
    let resolved = false;
    const callPromise = client.call({ method: 'ping', params: [] }).then(() => {
      resolved = true;
    });

    // Give microtasks a chance
    await new Promise((r) => setTimeout(r, 50));
    expect(resolved).toBe(false);
    expect(mockFetch).not.toHaveBeenCalled();

    // Simulate WS connect
    const connectCallback = mockWsClient.on.mock.calls.find(
      (c: any[]) => c[0] === 'connect',
    )?.[1];
    if (connectCallback) connectCallback();

    await callPromise;
    expect(resolved).toBe(true);
    expect(mockFetch).toHaveBeenCalled();
  });

  it('should not block if WS is already connected', async () => {
    mockWsClient.connected = true;

    vi.resetModules();
    const { RpcClient } = await import('@/clients/rpc');
    const client = new RpcClient();

    await client.call({ method: 'ping', params: [] });

    expect(mockFetch).toHaveBeenCalled();
  });

  it('should send x-session-id header from WS client id', async () => {
    mockWsClient.connected = true;
    mockWsClient.id = 'my-session-123';

    vi.resetModules();
    const { RpcClient } = await import('@/clients/rpc');
    const client = new RpcClient();

    await client.call({ method: 'test', params: [] });

    const fetchCall = mockFetch.mock.calls[0];
    const headers = fetchCall[1].headers;
    expect(headers['x-session-id']).toBe('my-session-123');
  });

  it('should send JSON-RPC 2.0 formatted request', async () => {
    mockWsClient.connected = true;

    vi.resetModules();
    const { RpcClient } = await import('@/clients/rpc');
    const client = new RpcClient();

    await client.call({ method: 'eth_getBalance', params: ['0x123'] });

    const fetchCall = mockFetch.mock.calls[0];
    const body = JSON.parse(fetchCall[1].body);
    expect(body.jsonrpc).toBe('2.0');
    expect(body.method).toBe('eth_getBalance');
    expect(body.params).toEqual(['0x123']);
    expect(body.id).toBeDefined();
  });

  it('should POST to the configured RPC URL', async () => {
    mockWsClient.connected = true;

    vi.resetModules();
    const { RpcClient } = await import('@/clients/rpc');
    const client = new RpcClient();

    await client.call({ method: 'ping', params: [] });

    const url = mockFetch.mock.calls[0][0];
    expect(url).toBe('http://localhost:4000/api');
  });
});
