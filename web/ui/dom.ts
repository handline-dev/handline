type Child = Node | string | null | false | undefined;
type Props = Record<string, unknown>;

/** Element builder: `h('button', { class: 'chip', onclick }, 'Esc')`. `on*` props are listeners; others are attributes. */
export function h<K extends keyof HTMLElementTagNameMap>(tag: K, props: Props | null = null, ...kids: Child[]): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props ?? {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v as EventListener);
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, String(v));
  }
  el.append(...kids.filter((c): c is Node | string => c !== null && c !== false && c !== undefined));
  return el;
}
