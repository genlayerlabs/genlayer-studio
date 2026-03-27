'use client';

import Link from '@/components/AppLink';
import { CopyButton } from '@/components/CopyButton';
import { truncateAddress, truncateHash } from '@/lib/formatters';
import { cn } from '@/lib/utils';

interface AddressDisplayProps {
  address: string;
  href?: string;
  truncate?: boolean;
  copyable?: boolean;
  className?: string;
  highlight?: boolean;
  onMouseEnter?: () => void;
  onMouseLeave?: () => void;
  /** Use hash-style truncation (10 start, 8 end) instead of address-style (8 start, 6 end) */
  isHash?: boolean;
  /** Custom start characters for truncation */
  truncateStart?: number;
  /** Custom end characters for truncation */
  truncateEnd?: number;
  /** Link className override */
  linkClassName?: string;
}

export function AddressDisplay({
  address,
  href,
  truncate: shouldTruncate = true,
  copyable = true,
  className,
  highlight = false,
  onMouseEnter,
  onMouseLeave,
  isHash = false,
  truncateStart,
  truncateEnd,
  linkClassName,
}: AddressDisplayProps) {
  const displayText = shouldTruncate
    ? isHash
      ? truncateHash(address, truncateStart, truncateEnd)
      : truncateAddress(address, truncateStart, truncateEnd)
    : address;

  const defaultLinkClassName = isHash
    ? 'text-primary hover:underline font-mono text-sm'
    : 'hover:text-primary';

  const textElement = href ? (
    <Link href={href} className={linkClassName ?? defaultLinkClassName}>
      {displayText}
    </Link>
  ) : (
    <span className="font-mono text-sm">{displayText}</span>
  );

  return (
    <div
      className={cn(
        'inline-flex items-center gap-1',
        highlight && 'px-1 -mx-1 rounded transition-colors bg-cyan-100 dark:bg-cyan-900/40',
        !highlight && onMouseEnter && 'px-1 -mx-1 rounded transition-colors',
        className
      )}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      {textElement}
      {copyable && <CopyButton text={address} />}
    </div>
  );
}
