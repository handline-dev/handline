// The primitives of ui/* and the rules the design system sets (docs/design-gaps.md §1): a state is a glyph, a control is labelled, a screen's
// css holds no colour of its own.
import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import {
  Button,
  Dialog,
  Empty,
  Field,
  Page,
  Popup,
  PopupRow,
  Segmented,
  Select,
  State,
  Switch,
  stateOf,
  type StateKind,
} from '../src/ui/index.ts';
import { Gallery } from '../src/Gallery.tsx';
import { DOT_ORDER, Mark } from '../src/RailIcons.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const show = async (el: React.ReactNode) => {
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => root!.render(el));
};
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  document.body.innerHTML = '';
});
const click = (el: Element | null) => act(async () => void (el as HTMLElement).click());
const press = (key: string, init: KeyboardEventInit = {}) =>
  act(async () => void dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, ...init })));

describe('State', () => {
  const KINDS: StateKind[] = ['working', 'booting', 'needs', 'blocked', 'idle', 'parked', 'done', 'noTerminal'];
  it('draws a different glyph with a tooltip and aria-label for every state, so no state is told by colour alone', async () => {
    await show(
      <>
        {KINDS.map((k) => (
          <State key={k} kind={k} />
        ))}
      </>,
    );
    const marks = [...box.querySelectorAll('.ui-state')].map(
      (s) => `${s.getAttribute('data-state')}|${s.querySelector('svg')!.getAttribute('class')}|${s.getAttribute('title')}`,
    );
    expect(new Set(marks.map((m) => m.split('|')[1])).size).toBe(KINDS.length);
    expect(box.querySelectorAll('[role="img"][aria-label]').length).toBeGreaterThanOrEqual(KINDS.length);
    expect($('.ui-state[data-state="noTerminal"] .ui-state-slash line')).not.toBeNull();
  });

  it("takes the screen's reason as the tooltip and prints the name with `word`", async () => {
    await show(<State kind="blocked" label="Blocked: waits for the build" word />);
    expect($('.ui-state')!.getAttribute('title')).toBe('Blocked: waits for the build');
    expect($('.ui-state-word')!.textContent).toBe('Blocked');
  });

  it("maps the daemon's state words, and a word it does not know is idle", () => {
    expect(stateOf('running')).toBe('working');
    expect(stateOf('WAITING')).toBe('needs');
    expect(stateOf('blocked')).toBe('blocked');
    expect(stateOf('parked')).toBe('parked'); // its own shape: a hollow ring would be idle's
    expect(stateOf('stopped')).toBe('idle');
    expect(stateOf('done')).toBe('done');
    expect(stateOf('pending')).toBe('idle'); // a pending task waits on the daemon, not on a person
    expect(stateOf('queued')).toBe('booting');
    expect(stateOf('something new')).toBe('idle');
  });
});

describe('the sidebar marks', () => {
  it('every agent state has its own shape, so none is told from another by colour alone (parked is not idle)', async () => {
    await show(
      <>
        {DOT_ORDER.map((d) => (
          <Mark key={d} dot={d} />
        ))}
      </>,
    );
    const shapes = [...box.querySelectorAll('.mark svg[data-icon]')].map((s) => s.getAttribute('data-icon'));
    expect(shapes).toHaveLength(DOT_ORDER.length);
    expect(new Set(shapes).size).toBe(DOT_ORDER.length);
  });
});

describe('Button', () => {
  it('an icon-only button is labelled and has its words as the tooltip; a solid one prints its shortcut', async () => {
    let n = 0;
    await show(
      <>
        <Button icon="refresh" label="Refresh" onClick={() => n++} />
        <Button variant="solid" label="Create" keys="⌘↵" />
      </>,
    );
    const icon = $('.ui-btn.icon-only')!;
    expect(icon.getAttribute('aria-label')).toBe('Refresh');
    expect(icon.getAttribute('title')).toBe('Refresh');
    await click(icon);
    expect(n).toBe(1);
    expect($('.ui-btn.solid')!.textContent).toContain('Create');
    expect($('.ui-btn.solid .ui-keys')!.textContent).toBe('⌘↵');
  });
});

describe('Field, Switch, Segmented, Empty', () => {
  it('a field ties its label and its note to the input, and an error note starts with an icon', async () => {
    await show(<Field label="Branch" note="No spaces" tone="bad" />);
    const input = $('input')!;
    expect($('label')!.getAttribute('for')).toBe(input.id);
    expect(input.getAttribute('aria-invalid')).toBe('true');
    expect($(`#${CSS.escape(input.getAttribute('aria-describedby')!)}`)!.querySelector('svg')).not.toBeNull();
  });

  it('a switch flips aria-checked and shows a mark when on', async () => {
    function T() {
      const [on, setOn] = useState(false);
      return <Switch on={on} onChange={setOn} label="Auto-park" />;
    }
    await show(<T />);
    expect($('[role="switch"]')!.getAttribute('aria-checked')).toBe('false');
    expect($('.ui-knob svg')).toBeNull();
    await click($('[role="switch"]'));
    expect($('[role="switch"]')!.getAttribute('aria-checked')).toBe('true');
    expect($('.ui-knob svg')).not.toBeNull();
  });

  it('a segmented control checks one option and reports the choice', async () => {
    const seen: string[] = [];
    await show(
      <Segmented
        label="Mode"
        value="agent"
        onChange={(v) => seen.push(v)}
        options={[
          { value: 'on', label: 'On' },
          { value: 'agent', label: 'Agent' },
          { value: 'off', label: 'Off' },
        ]}
      />,
    );
    expect([...box.querySelectorAll('[role="radio"]')].map((r) => r.getAttribute('aria-checked'))).toEqual(['false', 'true', 'false']);
    await click(box.querySelectorAll('[role="radio"]')[2]!);
    expect(seen).toEqual(['off']);
  });

  it('an empty state is an icon, a name and one hint', async () => {
    await show(<Empty icon="inbox" name="Nothing waits on you" hint="Results land here" />);
    expect($('.ui-empty-ring svg')).not.toBeNull();
    expect($('.ui-empty strong')!.textContent).toBe('Nothing waits on you');
  });
});

describe('Select, Page and PopupRow', () => {
  it('a select is labelled, has a chevron and reports the choice', async () => {
    const seen: string[] = [];
    await show(
      <Select
        label="Model"
        value="opus"
        onChange={(e) => seen.push(e.target.value)}
        options={[
          { value: 'opus', label: 'opus' },
          { value: 'sonnet', label: 'sonnet' },
        ]}
      />,
    );
    const sel = $('select') as HTMLSelectElement;
    expect(sel.getAttribute('aria-label')).toBe('Model');
    expect([...sel.options].map((o) => o.value)).toEqual(['opus', 'sonnet']);
    expect($('.ui-select svg')).not.toBeNull();
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(sel, 'sonnet');
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(seen).toEqual(['sonnet']);
  });

  it('a page is a 640 px column for a form and the full width for a list', async () => {
    await show(
      <>
        <Page>
          <p id="a">form</p>
        </Page>
        <Page width="wide">
          <p id="b">list</p>
        </Page>
      </>,
    );
    expect($('#a')!.parentElement!.className).toBe('ui-page form');
    expect($('#b')!.parentElement!.className).toBe('ui-page wide');
  });

  it('a popup row has its icon and name, and a chevron only when it opens a second popup', async () => {
    await show(
      <>
        <PopupRow icon="accounts" label="alpha" hint="41%" opens />
        <PopupRow label="gamma" />
      </>,
    );
    const [a, b] = [...box.querySelectorAll('.ui-prow')];
    expect(a!.querySelectorAll('svg')).toHaveLength(2);
    expect(a!.querySelector('[data-icon="chevronRight"]')).not.toBeNull();
    expect(a!.querySelector('.ui-prow-hint')!.textContent).toBe('41%');
    expect(b!.querySelectorAll('svg')).toHaveLength(0);
  });
});

describe('Popup and Dialog', () => {
  it('a popup closes on Escape and on a click outside, not on a click inside', async () => {
    let closed = 0;
    await show(
      <div>
        <span>
          <Popup open onClose={() => closed++} title="Accounts">
            <PopupRow label="alpha" opens />
          </Popup>
        </span>
        <p id="elsewhere">x</p>
      </div>,
    );
    expect($('[role="dialog"]')!.getAttribute('aria-label')).toBe('Accounts');
    expect($('.ui-prow svg')).not.toBeNull();
    await act(async () => void $('.ui-prow')!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true })));
    expect(closed).toBe(0);
    await press('Escape');
    expect(closed).toBe(1);
    await act(async () => void $('#elsewhere')!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true })));
    expect(closed).toBe(2);
  });

  it('a dialog has one solid action, closes on Escape, acts on the shortcut and puts focus inside', async () => {
    let closed = 0;
    let acted = 0;
    await show(
      <Dialog title="New task" onClose={() => closed++} action={{ label: 'Create', onClick: () => acted++ }}>
        <Field label="Title" />
      </Dialog>,
    );
    expect($('[role="dialog"]')!.getAttribute('aria-modal')).toBe('true');
    expect(document.activeElement?.tagName).toBe('INPUT');
    expect(box.querySelectorAll('.ui-btn.solid')).toHaveLength(1);
    await press('Enter', { metaKey: true });
    expect(acted).toBe(1);
    await press('Escape');
    expect(closed).toBe(1);
  });

  it('a dialog keeps focus where the person is while its parent re-renders with a new action object', async () => {
    const Host = () => {
      const [title, setTitle] = useState('');
      return (
        <Dialog title="New task" onClose={() => {}} action={{ label: 'Create', onClick: () => {} }}>
          <Field label="Owner" />
          <Field label="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
        </Dialog>
      );
    };
    await show(<Host />);
    const title = box.querySelectorAll('input')[1]!;
    await act(async () => title.focus());
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(title, 'a');
      title.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(title.value).toBe('a');
    expect(document.activeElement).toBe(title);
  });
});

describe('the gallery and the css rules', () => {
  it('the gallery draws every primitive', async () => {
    await show(<Gallery />);
    for (const sel of ['.ui-state', '.ui-btn.solid', '.ui-field', '.ui-select', '.ui-switch', '.ui-seg', '.ui-empty'])
      expect($(sel), sel).not.toBeNull();
  });

  const dir = join(import.meta.dirname, '../src/styles');
  const screens = readdirSync(dir).filter((f) => f.endsWith('.css') && f !== 'themes.css'); // themes.css overrides token values by design
  it('no screen css gives a colour a value: colours come from tokens.css', () => {
    for (const f of screens) expect(readFileSync(join(dir, f), 'utf8').match(/#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(/g) ?? [], f).toEqual([]);
  });
  it('styles.css is the tokens, then one file per screen, and every file is imported', () => {
    const index = readFileSync(join(import.meta.dirname, '../src/styles.css'), 'utf8');
    for (const f of screens) expect(index, f).toContain(`./styles/${f}`);
    expect(index.indexOf('./tokens.css')).toBeLessThan(index.indexOf('./styles/'));
  });

  // G06: a font size or a radius in px is a value the tokens already have. The screens still hold some; this count may only go down, and
  // ui.css holds none.
  const RAW: Record<string, [number, number]> = {
    'accounts.css': [2, 3],
    'agent-terminal.css': [3, 7],
    'base.css': [4, 5],
    'chat.css': [3, 2],
    'inbox.css': [5, 8],
    'rail.css': [3, 7],
    'workspace.css': [4, 4],
  };
  it('raw px font sizes and radii in screen css never grow, and the ui css has none', () => {
    for (const f of screens) {
      const css = readFileSync(join(dir, f), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
      const sizes = (css.match(/font-size:\s*[0-9.]+px/g) ?? []).length;
      const radii = (css.match(/border-radius:\s*[0-9.]+(?:px|%)/g) ?? []).length;
      const [maxSize, maxRadius] = RAW[f] ?? [0, 0];
      expect(sizes, `${f} font-size px`).toBeLessThanOrEqual(maxSize);
      expect(radii, `${f} border-radius px`).toBeLessThanOrEqual(maxRadius);
    }
  });
});
