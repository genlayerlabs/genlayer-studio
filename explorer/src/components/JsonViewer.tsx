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
    return (
      <span className="text-gray-500 italic">null</span>
    );
  }

  if (data === undefined) {
    return (
      <span className="text-gray-500 italic">undefined</span>
    );
  }

  if (typeof data === 'boolean') {
    return <span className="text-purple-600">{data.toString()}</span>;
  }

  if (typeof data === 'number') {
    return <span className="text-blue-600">{data}</span>;
  }

  if (typeof data === 'string') {
    // Check if it looks like an address or hash
    if (data.startsWith('0x')) {
      return <span className="text-green-600 font-mono text-sm">&quot;{data}&quot;</span>;
    }
    return <span className="text-amber-600">&quot;{data}&quot;</span>;
  }

  if (Array.isArray(data)) {
    if (data.length === 0) {
      return <span className="text-gray-500">[]</span>;
    }

    return (
      <div className="inline">
        <button
          onClick={() => setExpanded(!expanded)}
          className="inline-flex items-center text-gray-500 hover:text-gray-700"
        >
          {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          <span className="text-gray-400">[{data.length}]</span>
        </button>
        {expanded && (
          <div className="ml-4 border-l border-gray-200 pl-2">
            {data.map((item, index) => (
              <div key={index} className="py-0.5">
                <span className="text-gray-400 text-xs">{index}: </span>
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
      return <span className="text-gray-500">{'{}'}</span>;
    }

    return (
      <div className="inline">
        <div className="inline-flex items-center gap-1">
          <button
            onClick={() => setExpanded(!expanded)}
            className="inline-flex items-center text-gray-500 hover:text-gray-700"
          >
            {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
            {name && <span className="font-medium text-gray-700">{name}</span>}
            <span className="text-gray-400">{'{'}...{'}'}</span>
          </button>
          {level === 0 && (
            <button
              onClick={copyToClipboard}
              className="p-1 text-gray-400 hover:text-gray-600 rounded"
              title="Copy JSON"
            >
              {copied ? <Check className="w-3 h-3 text-green-500" /> : <Copy className="w-3 h-3" />}
            </button>
          )}
        </div>
        {expanded && (
          <div className="ml-4 border-l border-gray-200 pl-2">
            {entries.map(([key, value]) => (
              <div key={key} className="py-0.5">
                <span className="text-gray-600 font-medium">{key}: </span>
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
