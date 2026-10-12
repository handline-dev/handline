import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it } from 'vitest';
import { useRegisteredKeys, useScreenKeys, type KeyRows } from '../src/screenKeys.ts';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ROWS: KeyRows = [['x', 'do x']];
const Screen = () => (useScreenKeys('Screen', ROWS), null);
const Reader = ({ out }: { out: string[][] }) => (out.push(useRegisteredKeys().map((g) => g.title)), null);

it('a screen lists its keys while it is mounted and takes them away when it goes', async () => {
  const el = document.createElement('div');
  const root = createRoot(el);
  const seen: string[][] = [];
  await act(async () => root.render(<Reader out={seen} />));
  expect(seen.at(-1)).toEqual([]);
  await act(async () => root.render(<><Reader out={seen} /><Screen /></>));
  expect(seen.at(-1)).toEqual(['Screen']);
  await act(async () => root.render(<Reader out={seen} />));
  expect(seen.at(-1)).toEqual([]);
  await act(async () => root.unmount());
});
