'use client';

import { useEffect, useState } from 'react';
import { Separator } from '@/components/ui/separator';

interface StatsBarData {
  totalTransactions: number;
  totalValidators: number;
  totalContracts: number;
}

export function StatsBar() {
  const [stats, setStats] = useState<StatsBarData | null>(null);

  useEffect(() => {
    async function fetchStats() {
      try {
        const res = await fetch('/api/stats');
        if (!res.ok) return;
        const data = await res.json();
        setStats({
          totalTransactions: data.totalTransactions,
          totalValidators: data.totalValidators,
          totalContracts: data.totalContracts,
        });
      } catch {
        // Silently fail — stats bar is non-critical
      }
    }
    fetchStats();
  }, []);

  if (!stats) return null;

  return (
    <div className="border-b border-border bg-muted/50">
      <div className="container mx-auto px-4">
        <div className="flex items-center gap-4 h-8 text-xs text-muted-foreground">
          <span>Transactions: <span className="font-semibold text-foreground">{stats.totalTransactions.toLocaleString()}</span></span>
          <Separator orientation="vertical" className="h-3" />
          <span>Validators: <span className="font-semibold text-foreground">{stats.totalValidators.toLocaleString()}</span></span>
          <Separator orientation="vertical" className="h-3" />
          <span>Contracts: <span className="font-semibold text-foreground">{stats.totalContracts.toLocaleString()}</span></span>
          <Separator orientation="vertical" className="h-3" />
          <span className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
            Connected
          </span>
        </div>
      </div>
    </div>
  );
}
