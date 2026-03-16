import { createClient } from 'genlayer-js';
import { localnet } from 'genlayer-js/chains';

let client: ReturnType<typeof createClient> | null = null;

export function getClient() {
  if (!client) {
    client = createClient({
      chain: localnet,
      endpoint: '/api/rpc',
    });
  }
  return client;
}
