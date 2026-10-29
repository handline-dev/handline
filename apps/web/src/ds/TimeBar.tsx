// Time: what is left comes first. The bar fills per agent in two inks and hatches what is past the limit, so no share is told by hue alone.
import { Timer } from 'lucide-react';

export interface TimeShare {
  pct: number;
  tone: 1 | 2;
}

export function TimeBar({ shares, overPct = 0, label }: { shares: TimeShare[]; overPct?: number; label: string }) {
  return (
    <span role="img" aria-label={label} className="flex h-1.5 w-[220px] max-w-full overflow-hidden rounded-sm bg-raised">
      {shares.map((s, i) => (
        <i key={i} className={`block h-full ${s.tone === 1 ? 'bg-ink-2' : 'bg-ink-3'}`} style={{ width: `${s.pct}%` }} />
      ))}
      {overPct ? (
        <i
          className="block h-full"
          style={{ width: `${overPct}%`, background: 'repeating-linear-gradient(-45deg, var(--ink-2) 0 2px, transparent 2px 5px)' }}
        />
      ) : null}
    </span>
  );
}

/** "40m left", or "12m over" in the blocked hue beside its glyph. */
export function TimeLeft({ text, over }: { text: string; over?: boolean }) {
  return (
    <span className={`inline-flex items-center gap-[5px] text-sm ${over ? 'text-bad' : 'text-ink-2'}`}>
      <Timer width={15} height={15} strokeWidth={1.75} aria-hidden />
      {text}
    </span>
  );
}
