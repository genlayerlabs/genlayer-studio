const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:4000';

const BUSY_RETRIES = 2;
const DEFAULT_RETRY_DELAY_MS = 500;
const MAX_RETRY_DELAY_MS = 1000;

function retryDelayMs(retryAfter: string | null): number {
  const seconds = Number(retryAfter);
  if (!retryAfter || !Number.isFinite(seconds) || seconds < 0) {
    return DEFAULT_RETRY_DELAY_MS;
  }
  return Math.min(seconds * 1000, MAX_RETRY_DELAY_MS);
}

/**
 * Server-side fetch utility that calls the backend directly,
 * bypassing the Next.js middleware proxy hop.
 * Only use in Server Components or Route Handlers.
 */
export async function fetchBackend<T = unknown>(
  path: string,
): Promise<T> {
  const url = `${BACKEND_URL}/api/explorer${path}`;

  for (let attempt = 0; ; attempt++) {
    const res = await fetch(url, { cache: 'no-store' });

    // The backend sheds Explorer load with 503 + Retry-After.
    if (res.status === 503 && attempt < BUSY_RETRIES) {
      await res.body?.cancel();
      await new Promise((resolve) =>
        setTimeout(resolve, retryDelayMs(res.headers.get('retry-after'))),
      );
      continue;
    }

    if (!res.ok) {
      throw new Error(`Backend ${path}: ${res.status} ${res.statusText}`);
    }

    return res.json() as Promise<T>;
  }
}
