'use client';

import { useState, useMemo } from 'react';
import { b64ToArray, decodeCalldata } from '@/lib/resultDecoder';
import { JsonViewer } from '@/components/JsonViewer';
import { CodeBlock } from '@/components/CodeBlock';
import { Button } from '@/components/ui/button';
import { Code, FileText } from 'lucide-react';

interface DataDecodePanelProps {
  data: Record<string, unknown>;
}

type DecodedField =
  | { kind: 'calldata'; methodName: string; args: unknown[] }
  | { kind: 'text'; decoded: string; isCode: boolean };

function looksLikeBase64(value: unknown): boolean {
  if (typeof value !== 'string') return false;
  if (value.length < 24) return false;
  return /^[A-Za-z0-9+/=]+$/.test(value) || /^[A-Za-z0-9\-_=]+$/.test(value);
}

function tryDecodeField(key: string, value: unknown): DecodedField | null {
  if (!looksLikeBase64(value)) return null;

  // Use calldata-aware decoder for the `calldata` field
  if (key === 'calldata' && typeof value === 'string') {
    const result = decodeCalldata(value);
    if (result && result.methodName) return { kind: 'calldata', methodName: result.methodName, args: result.args };
  }

  const bytes = b64ToArray(value);
  if (bytes.length === 0) return null;

  try {
    const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    if (/[\x00-\x08\x0E-\x1F]/.test(text)) return null;
    const isCode = text.includes('class ') || text.includes('def ') || text.includes('import ');
    return { kind: 'text', decoded: text, isCode };
  } catch {
    return null;
  }
}

export function DataDecodePanel({ data }: DataDecodePanelProps) {
  const [mode, setMode] = useState<'decoded' | 'raw'>('decoded');

  const decodedFields = useMemo(() => {
    const fields: Record<string, DecodedField> = {};
    for (const [key, value] of Object.entries(data)) {
      const result = tryDecodeField(key, value);
      if (result) fields[key] = result;
    }
    return fields;
  }, [data]);

  const hasDecodable = Object.keys(decodedFields).length > 0;

  if (!hasDecodable) {
    return <JsonViewer data={data} />;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Button
          variant={mode === 'decoded' ? 'default' : 'outline'}
          size="sm"
          onClick={() => setMode('decoded')}
          className="gap-1.5"
        >
          <FileText className="w-3.5 h-3.5" />
          Decoded
        </Button>
        <Button
          variant={mode === 'raw' ? 'default' : 'outline'}
          size="sm"
          onClick={() => setMode('raw')}
          className="gap-1.5"
        >
          <Code className="w-3.5 h-3.5" />
          Raw
        </Button>
      </div>

      {mode === 'raw' ? (
        <JsonViewer data={data} />
      ) : (
        <div className="space-y-4">
          {Object.entries(data).map(([key, value]) => {
            const decoded = decodedFields[key];

            return (
              <div key={key}>
                <div className="text-xs font-semibold text-muted-foreground mb-1.5">{key}</div>
                {decoded ? (
                  decoded.kind === 'calldata' ? (
                    <div className="bg-muted p-3 rounded-lg space-y-2">
                      <div>
                        <span className="text-xs text-muted-foreground">Method</span>
                        <code className="block text-sm font-mono text-foreground mt-0.5">{decoded.methodName}</code>
                      </div>
                      {decoded.args.length > 0 && (
                        <div>
                          <span className="text-xs text-muted-foreground">Parameters</span>
                          <div className="mt-0.5">
                            <JsonViewer data={decoded.args} />
                          </div>
                        </div>
                      )}
                    </div>
                  ) : decoded.isCode ? (
                    <CodeBlock code={decoded.decoded} />
                  ) : (
                    <pre className="bg-muted p-3 rounded-lg text-sm font-mono whitespace-pre-wrap break-all overflow-auto max-h-96">
                      {decoded.decoded}
                    </pre>
                  )
                ) : (
                  <div className="bg-muted p-3 rounded-lg overflow-auto max-h-96">
                    <JsonViewer data={value} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
