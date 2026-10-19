// Two long multi-line Korean Markdown replies stay readable: each bubble has a fixed point width cap (a percentage let Android measure
// the text at one width and draw it at another, so the lines overlapped) and every block is drawn.
import { describe, expect, it } from 'vitest';
import { ARCH, $, mount, press, until } from './harness.tsx';

const LONG = (n: number) =>
  [`**T${n} 정리**`, '', '- #388 옛 답변 항목 제거: main과 충돌해서 고치는 중입니다', '- #355 필수 체크 이름을 새것으로 바꿔주세요', '', '1. 먼저 폰에서 직접 화면을 보시고 OK 해주셔야 합니다'].join('\n');

describe('long Markdown replies in the chat', () => {
  it('draw every block of two replies in bubbles capped in points', async () => {
    await mount((s) => {
      s.daemon.chat.seedItem(ARCH, { kind: 'reply', text: LONG(1) });
      s.daemon.chat.seedItem(ARCH, { kind: 'reply', text: LONG(2) });
    }, undefined, 'founder');
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => document.querySelectorAll('[data-testid^="reply-"]').length >= 2, 'both replies');
    const bubbles = [...document.querySelectorAll<HTMLElement>('[data-testid^="reply-"]')].filter((e) => /^reply-[^-]+$/.test(e.dataset.testid!) && e.textContent?.includes('정리'));
    expect(bubbles).toHaveLength(2);
    for (const b of bubbles) {
      expect(b.style.maxWidth).toMatch(/^\d+px$/);
      expect(b.textContent).toContain('정리');
      expect(b.textContent).toContain('먼저 폰에서');
    }
  });
});
