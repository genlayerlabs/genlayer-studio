'use client';

import { useState, useMemo } from 'react';
import { Download, Copy, Check } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { b64ToArray, decodeCalldata } from '@/lib/resultDecoder';

interface InputDataPanelProps {
  calldataB64: string;
}

type ViewMode = 'decoded' | 'hex' | 'raw';

function toHex(bytes: Uint8Array): string {
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
    .match(/.{1,2}/g)
    ?.join(' ') ?? '';
}

function serializeArg(val: unknown): unknown {
  if (typeof val === 'bigint') return val.toString();
  if (Array.isArray(val)) return val.map(serializeArg);
  if (val instanceof Map) {
    const obj: Record<string, unknown> = {};
    val.forEach((v, k) => { obj[String(k)] = serializeArg(v); });
    return obj;
  }
  return val;
}

export function InputDataPanel({ calldataB64 }: InputDataPanelProps) {
  const [mode, setMode] = useState<ViewMode>('decoded');
  const [copied, setCopied] = useState(false);

  const bytes = useMemo(() => b64ToArray(calldataB64), [calldataB64]);
  const decoded = useMemo(() => decodeCalldata(calldataB64), [calldataB64]);
  const hex = useMemo(() => toHex(bytes), [bytes]);

  const handleCopy = () => {
    let text = '';
    if (mode === 'hex') text = hex;
    else if (mode === 'raw') text = calldataB64;
    else if (decoded) text = JSON.stringify({ method: decoded.methodName, args: decoded.args.map(serializeArg) }, null, 2);
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const handleExportJson = () => {
    if (!decoded) return;
    const payload = { method: decoded.methodName, args: decoded.args.map(serializeArg) };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'input-data.json';
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="border border-border rounded-lg overflow-hidden">
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-border bg-muted/30">
        <div className="flex items-center gap-1">
          {(['decoded', 'hex', 'raw'] as ViewMode[]).map((v) => (
            <Button
              key={v}
              variant={mode === v ? 'default' : 'ghost'}
              size="sm"
              className="h-7 text-xs capitalize"
              onClick={() => setMode(v)}
            >
              {v}
            </Button>
          ))}
        </div>
        <div className="flex items-center gap-1">
          {decoded && (
            <Button variant="ghost" size="sm" className="h-7 text-xs gap-1.5" onClick={handleExportJson}>
              <Download className="w-3.5 h-3.5" />
              Export JSON
            </Button>
          )}
          <Button variant="ghost" size="sm" className="h-7 text-xs gap-1.5" onClick={handleCopy}>
            {copied ? <Check className="w-3.5 h-3.5 text-green-500" /> : <Copy className="w-3.5 h-3.5" />}
            {copied ? 'Copied' : 'Copy'}
          </Button>
        </div>
      </div>

      {/* Content */}
      <div className="p-4">
        {mode === 'decoded' && decoded ? (
          <div className="space-y-4">
            {/* Method — only for call transactions */}
            {decoded.methodName && (
              <div>
                <div className="text-xs text-muted-foreground mb-1 font-medium uppercase tracking-wide">Method</div>
                <code className="text-sm font-mono bg-muted px-2 py-1 rounded">{decoded.methodName}</code>
              </div>
            )}

            {/* Parameters / Constructor Arguments */}
            {decoded.args.length > 0 && (
              <div>
                <div className="text-xs text-muted-foreground mb-2 font-medium uppercase tracking-wide">
                  {decoded.methodName ? 'Parameters' : 'Constructor Arguments'}
                </div>
                <div className="border border-border rounded-lg overflow-hidden">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="bg-muted/50 border-b border-border">
                        <th className="text-left px-3 py-2 text-xs text-muted-foreground font-medium w-12">#</th>
                        <th className="text-left px-3 py-2 text-xs text-muted-foreground font-medium">Value</th>
                      </tr>
                    </thead>
                    <tbody>
                      {decoded.args.map((arg, i) => (
                        <tr key={i} className="border-b border-border last:border-0">
                          <td className="px-3 py-2 text-muted-foreground font-mono">{i}</td>
                          <td className="px-3 py-2 font-mono break-all">
                            {typeof arg === 'bigint'
                              ? arg.toString()
                              : typeof arg === 'string'
                              ? arg
                              : JSON.stringify(serializeArg(arg))}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        ) : mode === 'decoded' && !decoded ? (
          <p className="text-sm text-muted-foreground">Unable to decode calldata.</p>
        ) : mode === 'hex' ? (
          <pre className="text-xs font-mono text-foreground whitespace-pre-wrap break-all leading-relaxed">
            {hex || '(empty)'}
          </pre>
        ) : (
          <pre className="text-xs font-mono text-foreground break-all whitespace-pre-wrap">
            {calldataB64}
          </pre>
        )}
      </div>
    </div>
  );
}
