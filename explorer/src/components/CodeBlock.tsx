'use client';

import { useEffect, useState } from 'react';
import { useTheme } from 'next-themes';

interface CodeBlockProps {
  code: string;
  language?: string;
}

export function CodeBlock({ code, language = 'python' }: CodeBlockProps) {
  const [html, setHtml] = useState<string | null>(null);
  const { resolvedTheme } = useTheme();

  useEffect(() => {
    let cancelled = false;

    async function highlight() {
      try {
        const { codeToHtml } = await import('shiki');
        const theme = resolvedTheme === 'dark' ? 'github-dark' : 'github-light';
        const result = await codeToHtml(code, { lang: language, theme });
        if (!cancelled) setHtml(result);
      } catch {
        // Shiki failed — leave html null so fallback renders
      }
    }

    setHtml(null);
    highlight();
    return () => { cancelled = true; };
  }, [code, language, resolvedTheme]);

  if (html) {
    return (
      // biome-ignore lint/security/noDangerouslySetInnerHtml: Shiki output is trusted
      <div
        className="overflow-auto max-h-[600px] rounded-lg text-sm [&_pre]:p-4 [&_pre]:m-0"
        dangerouslySetInnerHTML={{ __html: html }}
      />
    );
  }

  // Loading / fallback: plain pre with pulse animation
  return (
    <div className="bg-muted text-foreground rounded-lg overflow-auto max-h-[600px]">
      <pre className="text-sm font-mono whitespace-pre p-4 animate-pulse">{code}</pre>
    </div>
  );
}
