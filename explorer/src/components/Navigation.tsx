'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { LayoutDashboard, ArrowRightLeft, Users, Database, Cpu, Layers } from 'lucide-react';

const navItems = [
  { href: '/', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/transactions', label: 'Transactions', icon: ArrowRightLeft },
  { href: '/validators', label: 'Validators', icon: Users },
  { href: '/state', label: 'Contract State', icon: Database },
  { href: '/providers', label: 'LLM Providers', icon: Cpu },
];

export function Navigation() {
  const pathname = usePathname();

  return (
    <nav className="bg-slate-900 text-white w-64 min-h-screen p-5 fixed left-0 top-0 flex flex-col">
      {/* Logo */}
      <div className="mb-8 pb-6 border-b border-slate-700/50">
        <Link href="/" className="flex items-center gap-3 group">
          <div className="bg-gradient-to-br from-blue-500 to-violet-600 p-2 rounded-xl shadow-lg shadow-blue-500/20">
            <Layers className="w-6 h-6 text-white" />
          </div>
          <div>
            <h1 className="text-lg font-bold text-white">Studio Explorer</h1>
            <p className="text-slate-400 text-xs">GenLayer State Browser</p>
          </div>
        </Link>
      </div>

      {/* Navigation Items */}
      <ul className="space-y-1 flex-1">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = pathname === item.href ||
            (item.href !== '/' && pathname.startsWith(item.href));

          return (
            <li key={item.href}>
              <Link
                href={item.href}
                className={`flex items-center gap-3 px-4 py-3 rounded-xl transition-all duration-200 ${
                  isActive
                    ? 'bg-gradient-to-r from-blue-600 to-blue-500 text-white shadow-lg shadow-blue-500/25'
                    : 'text-slate-300 hover:bg-slate-800/70 hover:text-white'
                }`}
              >
                <Icon className={`w-5 h-5 ${isActive ? 'text-white' : 'text-slate-400'}`} />
                <span className="font-medium">{item.label}</span>
              </Link>
            </li>
          );
        })}
      </ul>

      {/* Connection Status */}
      <div className="mt-auto pt-4">
        <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700/50">
          <div className="flex items-center gap-2 mb-2">
            <div className="w-2 h-2 bg-emerald-400 rounded-full animate-pulse shadow-lg shadow-emerald-400/50"></div>
            <span className="text-sm font-medium text-slate-200">Connected</span>
          </div>
          <div className="text-xs text-slate-400 font-mono">localhost:5432</div>
          <div className="text-xs text-slate-500 mt-1">genlayer_state</div>
        </div>
      </div>
    </nav>
  );
}
