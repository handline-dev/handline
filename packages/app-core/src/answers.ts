// A standing answer in words: the folder it trusts, or the option it picks when a dialog asks.
type Answer = { kind: string; key: readonly string[]; text: string };

export function answerLine(a: Answer): string {
  const [first = '', title, label] = a.key;
  if (a.kind === 'trust' && first) return `Trust the folder ${first} when an agent asks`;
  if (a.kind === 'dialog' && title && label) return `${first}: when asked "${title}", choose "${label}"`;
  return a.text;
}
