/**
 * Split a string into lines only at top-level (not inside strings or nested
 * braces/brackets). Preserves chunks such as JSON objects/arrays that may
 * span multiple lines.
 */
export function splitTopLevelLines(s: string): string[] {
  const out: string[] = [];
  let buf = '';
  let depthCurly = 0;
  let depthSquare = 0;
  let inString: '"' | "'" | null = null;
  let escaped = false;

  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    buf += ch;

    if (escaped) {
      escaped = false;
      continue;
    }
    if (inString && ch === '\\') {
      escaped = true;
      continue;
    }

    if (inString) {
      if (ch === inString) inString = null;
      continue;
    } else if (ch === '"' || ch === "'") {
      inString = ch as '"' | "'";
      continue;
    }

    if (ch === '{') depthCurly++;
    else if (ch === '}') depthCurly = Math.max(0, depthCurly - 1);
    else if (ch === '[') depthSquare++;
    else if (ch === ']') depthSquare = Math.max(0, depthSquare - 1);

    if ((ch === '\n' || ch === '\r') && depthCurly === 0 && depthSquare === 0) {
      const trimmed = buf.trim();
      if (trimmed) out.push(trimmed);
      buf = '';
    }
  }

  const tail = buf.trim();
  if (tail) out.push(tail);
  return out;
}
