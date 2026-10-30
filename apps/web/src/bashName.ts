/** What a Bash call runs, in a few words: `pnpm test` for `cd /app && FOO=1 pnpm test --filter web | tail`. '' when nothing is named. */
export function bashName(call: string): string {
  const parts = call
    .replace(/^Bash\s+/, '')
    .split(/&&|\|\||[;|\n]/)
    .map((p) => p.trim());
  const run = parts.find((p) => p && !/^(cd|pushd|popd|export|set|source|\.)(\s|$)/.test(p));
  const words = (run ?? '').split(/\s+/).filter((w) => w && !/^[A-Za-z_]\w*=/.test(w));
  const [first = '', ...rest] = words;
  const head = first.split('/').pop() ?? '';
  const args: string[] = [];
  for (const w of rest) {
    if (args.length >= 2 || /^[-"'$<>]|[/=]/.test(w)) break;
    args.push(w);
  }
  return [head, ...args].filter(Boolean).join(' ');
}
