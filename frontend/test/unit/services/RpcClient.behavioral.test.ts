/**
 * Behavioral snapshot tests for RpcClient.
 *
 * Documents the WS-blocking behavior and HTTP call pattern:
 * - On Studio (isStudio=true): block on WS connect before making HTTP call,
 *   forward session id in `x-session-id`.
 * - On non-Studio (isStudio=false): skip WS entirely — no session header.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockFetch = vi.fn();
const mockWsClient = {
  connected: false,
  id: 'test-session-id',
  on: vi.fn(),
};
const mockNetworkStore = {
  rpcUrl: 'http://localhost:4000/api',
  isStudio: true,
};

vi.mock('@/hooks', () => ({
  useWebSocketClient: vi.fn(() => mockWsClient),
}));

vi.mock('@/stores/network', () => ({
  useNetworkStore: vi.fn(() => mockNetworkStore),
}));

vi.mock('uuid', () => ({
  v4: vi.fn(() => 'test-uuid'),
}));

global.fetch = mockFetch as any;

describe('RpcClient — behavioral contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockWsClient.connected = false;
    mockWsClient.id = 'test-session-id';
    mockNetworkStore.rpcUrl = 'http://localhost:4000/api';
    mockNetworkStore.isStudio = true;
    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ result: 'ok', error: null }),
    });
  });

  it('on Studio: blocks on WS connect before making HTTP call', async () => {
    vi.resetModules();
    const { RpcClient } = await import('@/clients/rpc');
    const client = new RpcClient();

    let resolved = false;
    const callPromise = client.call({ method: 'ping', params: [] }).then(() => {
      resolved = true;
    });

    await new Promise((r) => setTimeout(r, 50));
    expect(resolved).toBe(false);
    expect(mockFetch).not.toHaveBeenCalled();

    const connectCallback = mockWsClient.on.mock.calls.find(
      (c: any[]) => c[0] === 'connect',
    )?.[1];
    if (connectCallback) connectCallback();

    await callPromise;
    expect(resolved).toBe(true);
    expect(mockFetch).toHaveBeenCalled();
  });

  it('on Studio: does not block if WS is already connected', async () => {
    mockWsClient.connected = true;

    vi.resetModules();
    const { RpcClient } = await import('@/clients/rpc');
    const client = new RpcClient();

    await client.call({ method: 'ping', params: [] });

    expect(mockFetch).toHaveBeenCalled();
  });

  it('on Studio: sends x-session-id header from WS client id', async () => {
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

  it('on non-Studio: skips WS entirely and omits the session header', async () => {
    mockNetworkStore.isStudio = false;

    vi.resetModules();
    const { RpcClient } = await import('@/clients/rpc');
    const client = new RpcClient();

    await client.call({ method: 'ping', params: [] });

    // No WS interactions at all
    expect(mockWsClient.on).not.toHaveBeenCalled();

    const fetchCall = mockFetch.mock.calls[0];
    const headers = fetchCall[1].headers;
    expect(headers['x-session-id']).toBeUndefined();
    expect(mockFetch).toHaveBeenCalled();
  });

  it('sends JSON-RPC 2.0 formatted request', async () => {
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

  it('POSTs to the RPC URL resolved from the network store', async () => {
    mockWsClient.connected = true;
    mockNetworkStore.rpcUrl = 'https://rpc-bradbury.genlayer.com';
    mockNetworkStore.isStudio = false;

    vi.resetModules();
    const { RpcClient } = await import('@/clients/rpc');
    const client = new RpcClient();

    await client.call({ method: 'ping', params: [] });

    const url = mockFetch.mock.calls[0][0];
    expect(url).toBe('https://rpc-bradbury.genlayer.com');
  });
});
