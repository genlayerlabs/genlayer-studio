const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:4000';

/**
 * Server-side fetch utility that calls the backend directly,
 * bypassing the Next.js middleware proxy hop.
 * Only use in Server Components or Route Handlers.
 */
export async function fetchBackend<T = unknown>(
  path: string,
): Promise<T> {
  const url = `${BACKEND_URL}/api/explorer${path}`;
  const res = await fetch(url, { cache: 'no-store' });

  if (!res.ok) {
    throw new Error(`Backend ${path}: ${res.status} ${res.statusText}`);
  }

  return res.json() as Promise<T>;
}
