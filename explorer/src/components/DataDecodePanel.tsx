'use client';

import { useState, useMemo } from 'react';
import { b64ToArray } from '@/lib/resultDecoder';
import { JsonViewer } from '@/components/JsonViewer';
import { CodeBlock } from '@/components/CodeBlock';
import { Button } from '@/components/ui/button';
import { Code, FileText } from 'lucide-react';

interface DataDecodePanelProps {
  data: Record<string, unknown>;
}

function looksLikeBase64(value: unknown): boolean {
  if (typeof value !== 'string') return false;
  if (value.length < 24) return false;
  // Strict base64 alphabet only (standard or URL-safe, not mixed with plain text)
  return /^[A-Za-z0-9+/=]+$/.test(value) || /^[A-Za-z0-9\-_=]+$/.test(value);
}

function tryDecodeField(value: unknown): { decoded: string; isCode: boolean } | null {
  if (!looksLikeBase64(value)) return null;

  const bytes = b64ToArray(value);
  if (bytes.length === 0) return null;

  try {
    const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    // Check if it's printable text (not binary garbage)
    if (/[\x00-\x08\x0E-\x1F]/.test(text)) return null;
    const isCode = text.includes('class ') || text.includes('def ') || text.includes('import ');
    return { decoded: text, isCode };
  } catch {
    return null;
  }
}

export function DataDecodePanel({ data }: DataDecodePanelProps) {
  const [mode, setMode] = useState<'decoded' | 'raw'>('decoded');

  const decodedFields = useMemo(() => {
    const fields: Record<string, { decoded: string; isCode: boolean }> = {};
    for (const [key, value] of Object.entries(data)) {
      const result = tryDecodeField(value);
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
                  decoded.isCode ? (
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
