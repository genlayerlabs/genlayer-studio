import { getRuntimeConfig } from '@/utils/runtimeConfig';

const API_KEY_STORAGE_KEY = 'settingsStore.apiKey';

export function getApiKey(): string | null {
  return localStorage.getItem(API_KEY_STORAGE_KEY);
}

export function getApiKeyHeaders(): Record<string, string> {
  const apiKey = getApiKey();
  return apiKey ? { 'X-API-Key': apiKey } : {};
}

/**
 * Patches globalThis.fetch to inject the X-API-Key header on requests
 * to the JSON-RPC endpoint. This is needed because the genlayer-js SDK
 * makes its own fetch calls with no extension point for custom headers.
 */
export function installApiKeyFetchInterceptor(): void {
  const rpcUrl = getRuntimeConfig(
    'VITE_JSON_RPC_SERVER_URL',
    'http://127.0.0.1:4000/api',
  );
  const originalFetch = globalThis.fetch;

  globalThis.fetch = function (
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> {
    const url = input instanceof Request ? input.url : input.toString();

    if (url === rpcUrl) {
      const apiKey = getApiKey();
      if (apiKey) {
        const headers = new Headers(init?.headers);
        if (!headers.has('X-API-Key')) {
          headers.set('X-API-Key', apiKey);
        }
        init = { ...init, headers };
      }
    }

    return originalFetch.call(globalThis, input, init);
  };
}
