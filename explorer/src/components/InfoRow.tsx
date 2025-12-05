'use client';

import { CopyButton } from './CopyButton';

interface InfoRowProps {
  label: string;
  value: React.ReactNode;
  copyable?: boolean;
  copyText?: string;
}

export function InfoRow({ label, value, copyable = false, copyText }: InfoRowProps) {
  const textToCopy = copyText || (typeof value === 'string' ? value : undefined);

  return (
    <div className="flex flex-col sm:flex-row sm:items-start py-3 border-b border-gray-100 last:border-0">
      <div className="text-gray-500 text-sm font-medium w-48 flex-shrink-0">{label}</div>
      <div className="flex items-center gap-2 flex-1 mt-1 sm:mt-0">
        <div className="font-mono text-sm break-all">{value}</div>
        {copyable && textToCopy && <CopyButton text={textToCopy} iconSize="md" />}
      </div>
    </div>
  );
}
