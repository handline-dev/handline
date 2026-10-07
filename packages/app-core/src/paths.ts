// Paths in a message the page may offer to open (the bridge's find_paths): `~/x` and an absolute path with an extension always; a relative
// one only if it holds a `/` or an extension. The page does not check the disk: the shell does, and a miss is "not found" on the click.

export interface PathSpan {
  start: number;
  end: number;
  /** What the shell is given: the text without a trailing `:42` or `:42:7`. */
  path: string;
}

const PATH = /(?:~\/|\/|\.{0,2}\/)?[\w.@+-]+(?:\/[\w.@+-]+)*(?:\/)?(?::\d+(?::\d+)?)?/g;
const hasExt = (s: string) => /\.[A-Za-z0-9]+$/.test(s.replace(/\/$/, ''));

export function findPaths(text: string): PathSpan[] {
  const out: PathSpan[] = [];
  for (const m of text.matchAll(PATH)) {
    const at = m.index!;
    // not part of a word, a URL or an email: the char before must be a boundary
    const before = text[at - 1];
    if (before && /[\w@:/.]/.test(before)) continue;
    let raw = m[0].replace(/[.,;)]+$/, '');
    raw = raw.replace(/:\d+(?::\d+)?$/, (x) => (raw.length === x.length ? x : ''));
    const path = raw;
    if (!path || /^[\d.]+$/.test(path) || (path.includes('@') && !path.includes('/'))) continue; // a number or an email
    const tilde = path.startsWith('~/');
    const abs = path.startsWith('/');
    const ok = tilde || abs ? hasExt(path) || tilde : path.includes('/') || hasExt(path);
    if (!ok || (abs && path.length < 2)) continue;
    const end = at + m[0].replace(/[.,;)]+$/, '').length;
    out.push({ start: at, end, path });
  }
  return out;
}
