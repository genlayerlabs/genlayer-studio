'use client';

import { useState, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import {
  Collapsible,
  CollapsibleTrigger,
  CollapsibleContent,
} from '@/components/ui/collapsible';
import { ChevronRight, Play, Loader2 } from 'lucide-react';
import {
  callReadMethod,
  parseParamValue,
  type ContractMethod,
} from '@/lib/contractSchema';

// ---------------------------------------------------------------------------
// MethodForm — a single collapsible method with inputs and (optional) execute
// ---------------------------------------------------------------------------

export function MethodForm({
  index,
  name,
  method,
  address,
  executable,
}: {
  index: number;
  name: string;
  method: ContractMethod;
  address: string;
  executable: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [inputs, setInputs] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {};
    for (const [pName] of method.params) init[pName] = '';
    return init;
  });
  const [result, setResult] = useState<unknown>(undefined);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleExecute = useCallback(async () => {
    setLoading(true);
    setError(null);
    setResult(undefined);
    try {
      const args = method.params.map(([pName, pType]) =>
        parseParamValue(inputs[pName] || '', pType)
      );
      const res = await callReadMethod(address, name, args);
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [address, name, method.params, inputs]);

  const hasParams = method.params.length > 0;

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="flex items-center gap-2 w-full text-left px-3 py-2.5 rounded-lg hover:bg-muted transition-colors group">
        <ChevronRight className={`w-4 h-4 text-muted-foreground transition-transform ${open ? 'rotate-90' : ''}`} />
        <span className="text-sm text-muted-foreground">{index}.</span>
        <span className="text-sm font-medium text-foreground">{name}</span>
        {!hasParams && executable && (
          <Badge variant="outline" className="text-xs ml-auto">no args</Badge>
        )}
        {method.payable && (
          <Badge className="bg-yellow-50 dark:bg-yellow-950 text-yellow-700 dark:text-yellow-400 border-yellow-200 dark:border-yellow-800 text-xs">
            payable
          </Badge>
        )}
      </CollapsibleTrigger>
      <CollapsibleContent className="pl-9 pr-3 pb-3">
        <div className="space-y-3 mt-1">
          {/* Parameter inputs */}
          {method.params.map(([pName, pType]) => (
            <div key={pName}>
              <label className="text-xs text-muted-foreground block mb-1">
                {pName} <span className="text-muted-foreground/60">({typeof pType === 'string' ? pType : JSON.stringify(pType)})</span>
              </label>
              {executable ? (
                <Input
                  value={inputs[pName] || ''}
                  onChange={(e) => setInputs((prev) => ({ ...prev, [pName]: e.target.value }))}
                  placeholder={pType === 'bool' ? 'true / false' : typeof pType === 'string' ? pType : JSON.stringify(pType)}
                  className="font-mono text-sm h-8"
                />
              ) : (
                <div className="bg-muted rounded-md px-3 py-1.5 text-sm font-mono text-muted-foreground">
                  {typeof pType === 'string' ? pType : JSON.stringify(pType)}
                </div>
              )}
            </div>
          ))}

          {/* Return type */}
          {method.ret && (
            <div className="text-xs text-muted-foreground">
              Returns: <code className="bg-muted px-1.5 py-0.5 rounded">{typeof method.ret === 'string' ? method.ret : JSON.stringify(method.ret)}</code>
            </div>
          )}

          {/* Execute button (read only) */}
          {executable && (
            <Button
              size="sm"
              onClick={handleExecute}
              disabled={loading}
              className="gap-1.5"
            >
              {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
              Query
            </Button>
          )}

          {/* Result */}
          {result !== undefined && (
            <div className="bg-muted rounded-lg p-3">
              <div className="text-xs text-muted-foreground mb-1">Result</div>
              <code className="text-sm font-mono text-foreground break-all">
                {formatResult(result)}
              </code>
            </div>
          )}

          {/* Error */}
          {error && (
            <div className="bg-red-50 dark:bg-red-950/50 border border-red-200 dark:border-red-800 rounded-lg p-3">
              <div className="text-xs text-red-600 dark:text-red-400 mb-1">Error</div>
              <code className="text-sm text-red-800 dark:text-red-300 break-all">{error}</code>
            </div>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function formatResult(val: unknown): string {
  if (val === null || val === undefined) return 'null';
  if (typeof val === 'bigint') return val.toString();
  if (val instanceof Map) {
    const obj: Record<string, unknown> = {};
    val.forEach((v, k) => { obj[String(k)] = v; });
    return JSON.stringify(obj, replacer, 2);
  }
  if (typeof val === 'object') {
    return JSON.stringify(val, replacer, 2);
  }
  return String(val);
}

export function replacer(_key: string, value: unknown): unknown {
  if (typeof value === 'bigint') return value.toString();
  return value;
}
