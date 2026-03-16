'use client';

import { useSearchParams, useRouter } from 'next/navigation';
import { useCallback } from 'react';

export function usePagination(searchParams: ReturnType<typeof useSearchParams>, defaultLimit: number = 20) {
  const router = useRouter();

  const page = parseInt(searchParams.get('page') || '1', 10) || 1;
  const limit = parseInt(searchParams.get('limit') || String(defaultLimit), 10) || defaultLimit;

  const updateParams = useCallback((updates: Record<string, string | null>) => {
    const params = new URLSearchParams(searchParams.toString());
    Object.entries(updates).forEach(([key, value]) => {
      if (value === null || value === '') {
        params.delete(key);
      } else {
        params.set(key, value);
      }
    });
    router.push(`?${params.toString()}`);
  }, [searchParams, router]);

  return { page, limit, updateParams };
}
