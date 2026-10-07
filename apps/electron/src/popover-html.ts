// The bottom-right popover's page: the new main's short sha and title, and two buttons. The title is a commit subject, so it is escaped.
import type { Main } from './follow.ts';

export const escapeHtml = (s: string): string =>
  s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);

/** `nonce` lets the one inline script run under the page's policy; it is new for every popover. */
export function popoverHtml(main: Main, nonce: string): string {
  return `<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}'">
<style>
:root{color-scheme:light dark;font:13px -apple-system,system-ui,sans-serif}
body{margin:0;padding:12px 14px;box-sizing:border-box;height:100vh;display:flex;flex-direction:column;gap:8px;background:Canvas;color:CanvasText;border:1px solid GrayText;border-radius:8px}
b{font-weight:600}.t{opacity:.8;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row{display:flex;gap:8px;justify-content:flex-end;margin-top:auto}button{font:inherit;padding:4px 12px}
</style>
<b>Main has a new version</b>
<div class="t"><code>${escapeHtml(main.sha.slice(0, 7))}</code> ${escapeHtml(main.title)}</div>
<div class="row"><button id="later">Later</button><button id="now" autofocus>Restart now</button></div>
<script nonce="${nonce}">document.getElementById('now').onclick=()=>window.followMain.restart();document.getElementById('later').onclick=()=>window.followMain.later();</script>`;
}
