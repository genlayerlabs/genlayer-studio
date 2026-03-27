'use client';

import { useState } from 'react';
import { ChevronDown, ChevronRight, Copy, Check } from 'lucide-react';

interface JsonViewerProps {
  data: unknown;
  name?: string;
  initialExpanded?: boolean;
  level?: number;
}

export function JsonViewer({ data, name, initialExpanded = true, level = 0 }: JsonViewerProps) {
  const [expanded, setExpanded] = useState(initialExpanded);
  const [copied, setCopied] = useState(false);

  const copyToClipboard = () => {
    navigator.clipboard.writeText(JSON.stringify(data, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (data === null) {
    return <span className="text-muted-foreground italic">null</span>;
  }

  if (data === undefined) {
    return <span className="text-muted-foreground italic">undefined</span>;
  }

  if (typeof data === 'boolean') {
    return <span className="text-purple-600 dark:text-purple-400">{data.toString()}</span>;
  }

  if (typeof data === 'number') {
    return <span className="text-blue-600 dark:text-blue-400">{data}</span>;
  }

  if (typeof data === 'string') {
    if (data.startsWith('0x')) {
      return <span className="text-green-600 dark:text-green-400 font-mono text-sm">&quot;{data}&quot;</span>;
    }
    return <span className="text-amber-600 dark:text-amber-400">&quot;{data}&quot;</span>;
  }

  if (Array.isArray(data)) {
    if (data.length === 0) {
      return <span className="text-muted-foreground">[]</span>;
    }

    return (
      <div className="inline">
        <button
          onClick={() => setExpanded(!expanded)}
          className="inline-flex items-center text-muted-foreground hover:text-foreground"
        >
          {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          <span className="text-muted-foreground">[{data.length}]</span>
        </button>
        {expanded && (
          <div className="ml-4 border-l border-border pl-2">
            {data.map((item, index) => (
              <div key={index} className="py-0.5">
                <span className="text-muted-foreground text-xs">{index}: </span>
                <JsonViewer data={item} level={level + 1} initialExpanded={level < 2} />
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  if (typeof data === 'object') {
    const entries = Object.entries(data);
    if (entries.length === 0) {
      return <span className="text-muted-foreground">{'{}'}</span>;
    }

    return (
      <div className="inline">
        <div className="inline-flex items-center gap-1">
          <button
            onClick={() => setExpanded(!expanded)}
            className="inline-flex items-center text-muted-foreground hover:text-foreground"
          >
            {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
            {name && <span className="font-medium text-foreground">{name}</span>}
            <span className="text-muted-foreground">{'{'}...{'}'}</span>
          </button>
          {level === 0 && (
            <button
              onClick={copyToClipboard}
              className="p-1 text-muted-foreground hover:text-foreground rounded"
              title="Copy JSON"
            >
              {copied ? <Check className="w-3 h-3 text-green-500" /> : <Copy className="w-3 h-3" />}
            </button>
          )}
        </div>
        {expanded && (
          <div className="ml-4 border-l border-border pl-2">
            {entries.map(([key, value]) => (
              <div key={key} className="py-0.5">
                <span className="text-muted-foreground font-medium">{key}: </span>
                <JsonViewer data={value} level={level + 1} initialExpanded={level < 1} />
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  return <span>{String(data)}</span>;
}
