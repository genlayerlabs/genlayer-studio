'use client';

import Link from 'next/link';
import { ChevronRight } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { cn } from '@/lib/utils';

interface StatCardProps {
  title: string;
  value: number | string;
  icon: React.ComponentType<{ className?: string }>;
  color: string;
  iconBg: string;
  href?: string;
}

export function StatCard({
  title,
  value,
  icon: Icon,
  color,
  iconBg,
  href,
}: StatCardProps) {
  const content = (
    <Card className={cn('hover:shadow-md transition-all duration-200 group', href && 'cursor-pointer')}>
      <CardContent className="p-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className={cn(iconBg, 'p-3 rounded-xl dark:opacity-90')}>
              <Icon className={cn('w-6 h-6', color)} />
            </div>
            <div>
              <p className="text-muted-foreground text-sm font-medium">{title}</p>
              <p className="text-2xl font-bold text-foreground mt-0.5">{value}</p>
            </div>
          </div>
          {href && (
            <ChevronRight className="w-5 h-5 text-muted-foreground/50 group-hover:text-muted-foreground transition-colors" />
          )}
        </div>
      </CardContent>
    </Card>
  );

  if (href) {
    return <Link href={href}>{content}</Link>;
  }
  return content;
}
