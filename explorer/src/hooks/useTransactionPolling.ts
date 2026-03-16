'use client';

import { useEffect } from 'react';
import { isTerminalStatus } from '@/lib/constants';
import type { TransactionStatus } from '@/lib/types';

interface HasTransactionStatus {
  transaction: { status: TransactionStatus };
}

export function useTransactionPolling<T extends HasTransactionStatus>(
  hash: string,
  data: T | null,
  setData: (d: T) => void,
) {
  useEffect(() => {
    if (!data || isTerminalStatus(data.transaction.status)) return;

    let timer: ReturnType<typeof setTimeout> | null = null;
    let active = true;

    function schedulePoll() {
      if (!active) return;
      timer = setTimeout(async () => {
        if (document.hidden || !active) return;
        try {
          const res = await fetch(`/api/transactions/${hash}`);
          if (res.ok) {
            const updated = await res.json();
            if (active) setData(updated);
            // Effect re-runs on setData, so don't self-schedule on success
            return;
          }
        } catch {
          // silently ignore polling errors
        }
        // Reschedule on error/non-ok so polling doesn't die
        schedulePoll();
      }, 5000);
    }

    function onVisibilityChange() {
      if (!document.hidden) {
        // Tab became visible — resume polling immediately
        if (timer) clearTimeout(timer);
        schedulePoll();
      }
    }

    schedulePoll();
    document.addEventListener('visibilitychange', onVisibilityChange);

    return () => {
      active = false;
      if (timer) clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [data, hash, setData]);
}
