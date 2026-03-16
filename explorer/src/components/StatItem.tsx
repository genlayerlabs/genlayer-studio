import React from 'react';

export function StatItem({ icon, iconBg, label, value, small }: {
  icon: React.ReactNode;
  iconBg: string;
  label: string;
  value: string;
  small?: boolean;
}) {
  return (
    <div className="flex items-center gap-3">
      <div className={`${iconBg} p-2 rounded-lg`}>{icon}</div>
      <div>
        <p className="text-muted-foreground text-sm">{label}</p>
        {small
          ? <p className="text-sm font-medium text-foreground">{value}</p>
          : <p className="text-xl font-bold text-foreground">{value}</p>
        }
      </div>
    </div>
  );
}
