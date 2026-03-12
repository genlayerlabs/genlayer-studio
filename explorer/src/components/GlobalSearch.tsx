'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { Search, ArrowRightLeft, Database, Loader2, Clock, Users } from 'lucide-react';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { StatusBadge } from '@/components/StatusBadge';
import { Badge } from '@/components/ui/badge';
import { truncateHash, truncateAddress } from '@/lib/formatters';
import type { Transaction, CurrentState, Validator, TransactionStatus } from '@/lib/types';

interface SearchResults {
  transactions: Transaction[];
  states: CurrentState[];
  validators: Validator[];
}

export function GlobalSearch() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResults>({ transactions: [], states: [], validators: [] });
  const [loading, setLoading] = useState(false);
  const router = useRouter();
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Cmd+K / Ctrl+K listener
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setOpen((prev) => !prev);
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, []);

  // Focus input when dialog opens; clean up debounce on unmount
  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 0);
    } else {
      setQuery('');
      setResults({ transactions: [], states: [], validators: [] });
    }
  }, [open]);

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  const search = useCallback(async (q: string) => {
    if (!q.trim()) {
      setResults({ transactions: [], states: [], validators: [] });
      setLoading(false);
      return;
    }

    // Cancel any in-flight request
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    try {
      const encoded = encodeURIComponent(q.trim());
      const [txRes, stateRes, valRes] = await Promise.all([
        fetch(`/api/transactions?search=${encoded}&limit=5`, { signal: controller.signal }),
        fetch(`/api/contracts?search=${encoded}&limit=5`, { signal: controller.signal }),
        fetch(`/api/validators?search=${encoded}&limit=5`, { signal: controller.signal }),
      ]);

      const txData = txRes.ok ? await txRes.json() : { transactions: [] };
      const stateData = stateRes.ok ? await stateRes.json() : { states: [] };
      const valData = valRes.ok ? await valRes.json() : { validators: [] };

      setResults({
        transactions: txData.transactions || [],
        states: stateData.states || [],
        validators: valData.validators || [],
      });
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      setResults({ transactions: [], states: [], validators: [] });
    } finally {
      setLoading(false);
    }
  }, []);

  const handleInputChange = (value: string) => {
    setQuery(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => search(value), 300);
  };

  const navigate = (path: string) => {
    setOpen(false);
    router.push(path);
  };

  const hasResults = results.transactions.length > 0 || results.states.length > 0 || results.validators.length > 0;

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-md border border-border bg-muted/50 text-muted-foreground text-sm hover:bg-accent hover:text-foreground transition-colors"
      >
        <Search className="w-4 h-4" />
        <span className="hidden lg:inline">Search...</span>
        <kbd className="pointer-events-none hidden lg:inline-flex h-5 select-none items-center gap-1 rounded border border-border bg-muted px-1.5 font-mono text-[10px] font-medium text-muted-foreground">
          {typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.userAgent) ? '⌘K' : 'Ctrl+K'}
        </kbd>
      </button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-lg p-0 gap-0 overflow-hidden outline-none focus:outline-none">
          <DialogTitle className="sr-only">Search</DialogTitle>
          <div className="flex items-center border-b border-border px-3">
            <Search className="w-4 h-4 text-muted-foreground shrink-0" />
            <input
              ref={inputRef}
              type="text"
              placeholder="Search transactions and contracts..."
              value={query}
              onChange={(e) => handleInputChange(e.target.value)}
              className="flex-1 h-11 bg-transparent px-3 text-sm text-foreground placeholder:text-muted-foreground outline-none border-none [box-shadow:none!important]"
            />
            {loading && <Loader2 className="w-4 h-4 animate-spin text-muted-foreground shrink-0" />}
          </div>

          {!query.trim() && (
            <div className="p-2">
              <div className="px-3 py-1.5 text-xs font-semibold text-muted-foreground">
                Quick Links
              </div>
              {[
                { icon: ArrowRightLeft, label: 'All Transactions', href: '/transactions' },
                { icon: Clock, label: 'In Progress Transactions', href: '/transactions?tab=in_progress' },
                { icon: Database, label: 'All Contracts', href: '/contracts' },
                { icon: Users, label: 'All Validators', href: '/validators' },
              ].map((link) => (
                <button
                  key={link.href}
                  onClick={() => navigate(link.href)}
                  className="w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm hover:bg-accent transition-colors text-left cursor-pointer"
                >
                  <link.icon className="w-4 h-4 text-muted-foreground" />
                  <span className="text-foreground">{link.label}</span>
                </button>
              ))}
            </div>
          )}

          {query.trim() && (
            <div className="max-h-80 overflow-y-auto p-2">
              {!loading && !hasResults && (
                <div className="px-3 py-6 text-center text-sm text-muted-foreground">
                  No results found
                </div>
              )}

              {results.transactions.length > 0 && (
                <div>
                  <div className="px-3 py-1.5 text-xs font-semibold text-muted-foreground flex items-center gap-1.5">
                    <ArrowRightLeft className="w-3 h-3" />
                    Transactions
                  </div>
                  {results.transactions.map((tx) => (
                    <button
                      key={tx.hash}
                      onClick={() => navigate(`/transactions/${tx.hash}`)}
                      className="w-full flex items-center justify-between px-3 py-2 rounded-md text-sm hover:bg-accent transition-colors text-left cursor-pointer"
                    >
                      <span className="font-mono text-foreground">{truncateHash(tx.hash)}</span>
                      <StatusBadge status={tx.status} />
                    </button>
                  ))}
                </div>
              )}

              {results.states.length > 0 && (
                <div className={results.transactions.length > 0 ? 'mt-2' : ''}>
                  <div className="px-3 py-1.5 text-xs font-semibold text-muted-foreground flex items-center gap-1.5">
                    <Database className="w-3 h-3" />
                    Contracts
                  </div>
                  {results.states.map((state) => (
                    <button
                      key={state.id}
                      onClick={() => navigate(`/address/${state.id}`)}
                      className="w-full flex items-center justify-between px-3 py-2 rounded-md text-sm hover:bg-accent transition-colors text-left cursor-pointer"
                    >
                      <span className="font-mono text-foreground">{truncateAddress(state.id)}</span>
                      <Badge variant="secondary">Contract</Badge>
                    </button>
                  ))}
                </div>
              )}

              {results.validators.length > 0 && (
                <div className={(results.transactions.length > 0 || results.states.length > 0) ? 'mt-2' : ''}>
                  <div className="px-3 py-1.5 text-xs font-semibold text-muted-foreground flex items-center gap-1.5">
                    <Users className="w-3 h-3" />
                    Validators
                  </div>
                  {results.validators.map((v) => (
                    <button
                      key={v.id}
                      onClick={() => navigate(v.address ? `/address/${v.address}` : '/validators')}
                      className="w-full flex items-center justify-between px-3 py-2 rounded-md text-sm hover:bg-accent transition-colors text-left cursor-pointer"
                    >
                      <span className="font-mono text-foreground">{v.address ? truncateAddress(v.address) : `Validator #${v.id}`}</span>
                      <Badge variant="secondary">{v.provider}/{v.model}</Badge>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
