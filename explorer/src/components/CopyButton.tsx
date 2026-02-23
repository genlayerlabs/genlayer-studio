'use client';

import { useState } from 'react';
import { Copy, Check } from 'lucide-react';

interface CopyButtonProps {
  text: string;
  className?: string;
  iconSize?: 'sm' | 'md';
}

export function CopyButton({ text, className = '', iconSize = 'sm' }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  const copy = () => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const sizeClasses = iconSize === 'sm' ? 'w-3.5 h-3.5' : 'w-4 h-4';

  return (
    <button
      onClick={copy}
      className={`p-1 text-slate-400 hover:text-slate-600 rounded transition-colors ${className}`}
      title="Copy to clipboard"
    >
      {copied ? (
        <Check className={`${sizeClasses} text-green-500`} />
      ) : (
        <Copy className={sizeClasses} />
      )}
    </button>
  );
}
