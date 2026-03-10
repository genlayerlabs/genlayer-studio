'use client';

import { CheckCircle, XCircle, Clock, AlertCircle } from 'lucide-react';

interface VoteIconProps {
  vote: string | undefined;
  className?: string;
}

export function VoteIcon({ vote, className = 'w-4 h-4' }: VoteIconProps) {
  switch (vote) {
    case 'agree':
      return <CheckCircle className={`${className} text-green-500 dark:text-green-400`} />;
    case 'disagree':
      return <XCircle className={`${className} text-red-500 dark:text-red-400`} />;
    case 'timeout':
      return <Clock className={`${className} text-yellow-500 dark:text-yellow-400`} />;
    default:
      return <AlertCircle className={`${className} text-muted-foreground`} />;
  }
}
