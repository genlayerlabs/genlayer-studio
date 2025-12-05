'use client';

import Link from 'next/link';
import { ChevronRight } from 'lucide-react';

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
    <div className="bg-white rounded-xl border border-slate-200 p-5 hover:border-slate-300 hover:shadow-md transition-all duration-200 group">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <div className={`${iconBg} p-3 rounded-xl`}>
            <Icon className={`w-6 h-6 ${color}`} />
          </div>
          <div>
            <p className="text-slate-600 text-sm font-medium">{title}</p>
            <p className="text-2xl font-bold text-slate-900 mt-0.5">{value}</p>
          </div>
        </div>
        {href && (
          <ChevronRight className="w-5 h-5 text-slate-300 group-hover:text-slate-400 transition-colors" />
        )}
      </div>
    </div>
  );

  if (href) {
    return <Link href={href}>{content}</Link>;
  }
  return content;
}
