export type Diagnostic = {
  line: number;
  col: number;
  message: string;
  severity: 'warning' | 'error';
};

const RULES: Array<{ re: RegExp; msg: string }> = [
  { re: /\brandom\./, msg: 'Avoid randomness in contracts.' },
  { re: /\bsecrets?\./, msg: 'Avoid non-deterministic secrets.' },
  { re: /\btime\.(time|sleep|ctime|strftime)/, msg: 'Avoid wall-clock time.' },
  { re: /\bdatetime\./, msg: 'Avoid wall-clock time.' },
  { re: /\brequests?\./, msg: 'No network I/O in contracts.' },
  { re: /\burllib\./, msg: 'No network I/O in contracts.' },
  { re: /\bopen\s*\(/, msg: 'No filesystem I/O in contracts.' },
  { re: /\bsubprocess\./, msg: 'No subprocess in contracts.' },
];

export function lintDeterminism(source: string): Diagnostic[] {
  const lines = source.split(/\r?\n/);
  const diags: Diagnostic[] = [];
  lines.forEach((ln, i) => {
    for (const r of RULES) {
      const m = r.re.exec(ln);
      if (m) diags.push({ line: i, col: m.index, message: r.msg, severity: 'error' });
    }
  });
  return diags;
}


