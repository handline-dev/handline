// The window that shows a phone the one-time pairing QR. Static: the QR is an inline SVG and there is no script.
import qrcode from 'qrcode-generator';

export const escapeHtml = (s: string): string =>
  s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);

/** The link as an SVG QR; the page's colours decide light or dark. */
export function qrSvg(uri: string): string {
  const q = qrcode(0, 'M');
  q.addData(uri);
  q.make();
  return q.createSvgTag({ cellSize: 6, margin: 0, scalable: true });
}

/** `until` is the clock time the link stops working. The button is a link the window's owner catches and copies from; the page has no script. */
export const COPY_URL = 'handline-copy://link';

export function phoneHtml(uri: string, minutes: number, until = ''): string {
  return `<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
<style>
:root{color-scheme:light dark;font:13px -apple-system,system-ui,sans-serif}
body{margin:0;padding:20px;box-sizing:border-box;text-align:center;background:Canvas;color:CanvasText}
.qr{width:240px;height:240px;margin:12px auto;padding:12px;background:#fff;border-radius:8px;box-sizing:content-box}
.qr svg{width:100%;height:100%}.s{opacity:.7}
a.copy{display:inline-block;margin:8px 0;padding:5px 14px;border:1px solid;border-radius:6px;color:inherit;text-decoration:none}
</style>
<b>Pair a phone</b>
<div class="qr">${qrSvg(uri)}</div>
<div>Open Handline on the phone and scan this code.</div>
<div>Or <a class="copy" id="c" href="${COPY_URL}">Copy link</a> and send it to the phone.</div>
<div class="s">The code and the link work once, for ${escapeHtml(String(minutes))} minutes${until ? `, until ${escapeHtml(until)}` : ''}. Close this window when the phone is connected.</div>`;
}
