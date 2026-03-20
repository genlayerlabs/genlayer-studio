import NextLink from 'next/link';
import type { ComponentProps } from 'react';

/**
 * App-wide Link wrapper that disables Next.js automatic prefetching by default.
 * This prevents dozens of _rsc requests firing on pages with many links (e.g. transaction tables).
 */
export default function AppLink({ prefetch = false, ...props }: ComponentProps<typeof NextLink>) {
  return <NextLink prefetch={prefetch} {...props} />;
}
