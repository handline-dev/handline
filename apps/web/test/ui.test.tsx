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
  Popup,
  PopupRow,
  Segmented,
  Select,
  State,
  Switch,
  stateOf,
} from '../src/ui/index.ts';
import { DOT_ORDER, Mark } from '../src/RailIcons.tsx';
import { AgentGauges } from '../src/AgentGauges.tsx';

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
  act(async () => void document.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, ...init })));

describe('State', () => {
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

describe('the agent gauges', () => {
  it('context fills with use and the cache drains with its minutes, each behind its own icon', async () => {
    await show(<AgentGauges ctx={34} cacheMin={15} />);
    const meters = [...box.querySelectorAll('[role=meter]')];
    expect(meters.map((m) => m.getAttribute('aria-valuenow'))).toEqual(['34', '25']);
    expect([...box.querySelectorAll('svg[data-icon]')].map((i) => i.getAttribute('data-icon'))).toEqual(['context', 'cache']);
  });

  it('shows a lapsed cache as an empty bar and nothing for a reading it does not have', async () => {
    await show(<AgentGauges ctx={null} cacheMin={0} />);
    expect(box.querySelectorAll('[role=meter]')).toHaveLength(1);
    expect($('[role=meter]')!.getAttribute('aria-valuenow')).toBe('0');
    await show(<AgentGauges ctx={null} cacheMin={null} />);
    expect(box.querySelectorAll('[role=meter]')).toHaveLength(0);
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
    for (let i = 0; i < 20 && document.activeElement?.tagName !== 'INPUT'; i++) {
      await act(async () => void (await new Promise((r) => setTimeout(r, 10)))); // Base UI moves focus in on the next frame
    }
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

describe('the css files', () => {
  const dir = join(import.meta.dirname, '../src/styles');
  const screens = readdirSync(dir).filter((f) => f.endsWith('.css') && f !== 'themes.css'); // themes.css overrides token values by design
  it('styles.css is the tokens, then one file per screen, and every file is imported', () => {
    const index = readFileSync(join(import.meta.dirname, '../src/styles.css'), 'utf8');
    for (const f of screens) expect(index, f).toContain(`./styles/${f}`);
    expect(index.indexOf('./tokens.css')).toBeLessThan(index.indexOf('./styles/'));
  });
});
