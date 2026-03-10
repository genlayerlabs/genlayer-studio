'use client';

import { useState } from 'react';
import { Copy, Check } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

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
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          onClick={copy}
          aria-label={copied ? 'Copied' : 'Copy to clipboard'}
          className={cn('h-6 w-6 text-muted-foreground hover:text-foreground', className)}
        >
          {copied ? (
            <Check className={cn(sizeClasses, 'text-green-500')} />
          ) : (
            <Copy className={sizeClasses} />
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent>
        <p>{copied ? 'Copied!' : 'Copy to clipboard'}</p>
      </TooltipContent>
    </Tooltip>
  );
}
