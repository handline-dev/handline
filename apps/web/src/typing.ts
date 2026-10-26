// Whether a key event came from a place where letters are text: a field or the rich editor.
export const typing = (t: EventTarget | null) => t instanceof HTMLElement && !!t.closest('input, textarea, select, [contenteditable]');
