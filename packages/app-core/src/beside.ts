// The two-agent split: the page shows one agent, or two side by side (the second, `beside`, rides in the route). Dropping an agent on a half decides who is shown.
export interface Pair {
  agent: string;
  beside: string | null;
}
export type Half = 'left' | 'right';

/**
 * What the page shows after `dragged` is dropped on a half, or null when nothing changes. The right half takes it as the second agent; the left half
 * replaces the first, keeping the second. Dropping the agent the other half shows swaps the two.
 */
export function dropAgent(now: Pair, half: Half, dragged: string): Pair | null {
  const { agent, beside } = now;
  if (half === 'right') {
    if (dragged === beside) return null;
    return dragged === agent ? (beside ? { agent: beside, beside: agent } : null) : { agent, beside: dragged };
  }
  if (dragged === agent) return null;
  return dragged === beside ? { agent: dragged, beside: agent } : { agent: dragged, beside };
}
