// The grey line a solved daemon stall leaves belongs to the agent it was seen on.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { useSolvedStall } from '#src/ChatCards.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function Probe({ agent, title }: { agent: string; title: string | null }) {
  const solved = useSolvedStall(agent, title);
  return <p>{solved ? `solved:${solved.title}` : 'none'}</p>;
}

let root: Root | null = null;
let box: HTMLElement;
const show = (agent: string, title: string | null) => act(async () => root!.render(<Probe agent={agent} title={title} />));
const out = () => box.textContent;

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('useSolvedStall', () => {
  it('keeps the title of a stall that ended on the same agent', async () => {
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await show('A', 'Waiting for folder trust');
    expect(out()).toBe('none');
    await show('A', null);
    expect(out()).toBe('solved:Waiting for folder trust');
    await show('A', "Won't start");
    expect(out()).toBe('none');
  });

  it('shows nothing on another agent that has no stall, in the same mounted component', async () => {
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await show('A', 'Waiting for folder trust');
    await show('B', null);
    expect(out()).toBe('none');
    await show('A', null);
    expect(out()).toBe('none');
  });

  it('shows nothing on another agent after the stall on the first has ended', async () => {
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await show('A', 'Waiting for folder trust');
    await show('A', null);
    expect(out()).toBe('solved:Waiting for folder trust');
    await show('B', null);
    expect(out()).toBe('none');
  });
});
