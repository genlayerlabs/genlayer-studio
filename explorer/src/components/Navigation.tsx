'use client';

import Link from '@/components/AppLink';
import Image from 'next/image';
import { usePathname } from 'next/navigation';
import { LayoutDashboard, ArrowRightLeft, Users, Database, Cpu } from 'lucide-react';
import { GlobalSearch } from './GlobalSearch';
import { ThemeToggle } from './ThemeToggle';
import { cn } from '@/lib/utils';

const navItems = [
  { href: '/', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/txs', label: 'Transactions', icon: ArrowRightLeft },
  { href: '/validators', label: 'Validators', icon: Users },
  { href: '/contracts', label: 'Contracts', icon: Database },
  { href: '/providers', label: 'Providers', icon: Cpu },
];

export function Navigation() {
  const pathname = usePathname();

  return (
    <nav className="sticky top-0 z-50 border-b border-border bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="container mx-auto px-4">
        <div className="flex h-14 items-center justify-between gap-4">
          {/* Logo */}
          <Link href="/" className="flex items-center gap-2 shrink-0">
            <Image src="/genlayer-logo.svg" alt="GenLayer Logo" width={28} height={28} className="dark:invert" />
            <span className="font-bold text-foreground hidden sm:inline" style={{ fontFamily: 'Switzer, sans-serif' }}>GenLayer Studio Explorer</span>
          </Link>

          {/* Nav Links */}
          <div className="flex items-center gap-1">
            {navItems.map((item) => {
              const Icon = item.icon;
              const isActive = pathname === item.href ||
                (item.href !== '/' && pathname.startsWith(item.href));

              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={cn(
                    'flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors',
                    isActive
                      ? 'bg-accent text-foreground'
                      : 'text-muted-foreground hover:bg-accent hover:text-foreground'
                  )}
                >
                  <Icon className="w-4 h-4" />
                  <span className="hidden md:inline">{item.label}</span>
                </Link>
              );
            })}
          </div>

          {/* Right side: Search + Theme */}
          <div className="flex items-center gap-2 shrink-0">
            <GlobalSearch />
            <ThemeToggle />
          </div>
        </div>
      </div>
    </nav>
  );
}
