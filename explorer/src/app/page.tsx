import { Suspense } from 'react';
import { StatCardsSection, ChartsSection, RecentTransactionsSection } from './DashboardSections';

function CardSkeleton({ className = '' }: { className?: string }) {
  return <div className={`bg-muted/50 animate-pulse rounded-lg ${className}`} />;
}

export default function DashboardPage() {
  return (
    <div className="space-y-4 animate-fade-in">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Dashboard</h1>
        <p className="text-sm text-muted-foreground">Overview of GenLayer state and transactions</p>
      </div>

      <Suspense
        fallback={
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
            {Array.from({ length: 5 }, (_, i) => (
              <CardSkeleton key={i} className="h-[88px]" />
            ))}
          </div>
        }
      >
        <StatCardsSection />
      </Suspense>

      <Suspense
        fallback={
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {Array.from({ length: 3 }, (_, i) => (
              <CardSkeleton key={i} className="h-[140px]" />
            ))}
          </div>
        }
      >
        <ChartsSection />
      </Suspense>

      <Suspense fallback={<CardSkeleton className="h-[300px]" />}>
        <RecentTransactionsSection />
      </Suspense>
    </div>
  );
}
