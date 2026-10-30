// Two long multi-line Korean Markdown messages stay readable: each bubble has a fixed point width cap (a percentage let Android measure
// the text at one width and draw it at another, so the lines overlapped) and every block is drawn.
import { describe, expect, it } from 'vitest';
import { ARCH, $, mount, press, until } from '#test/harness.tsx';

const LONG = (n: number) =>
  [
    `**T${n} 정리**`,
    '',
    '- #388 옛 답변 항목 제거: main과 충돌해서 고치는 중입니다',
    '- #355 필수 체크 이름을 새것으로 바꿔주세요',
    '',
    '1. 먼저 폰에서 직접 화면을 보시고 OK 해주셔야 합니다',
  ].join('\n');

describe('long Markdown messages in the chat', () => {
  it('draw every block of two messages in bubbles capped in points', async () => {
    await mount(
      (s) => {
        s.daemon.chat.seed(ARCH, { body: LONG(1), from: ARCH, to: 'human' });
        s.daemon.chat.seed(ARCH, { body: LONG(2), from: ARCH, to: 'human' });
      },
      undefined,
      'founder',
    );
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => document.querySelectorAll('[data-testid^="body-"]').length >= 2, 'both messages');
    const bodies = [...document.querySelectorAll<HTMLElement>('[data-testid^="body-"]')].filter((e) => e.textContent?.includes('정리'));
    expect(bodies).toHaveLength(2);
    for (const body of bodies) {
      const bubble = body.parentElement as HTMLElement; // the bubble View holds the body
      expect(bubble.style.maxWidth).toMatch(/^\d+px$/);
      expect(body.textContent).toContain('먼저 폰에서');
    }
  });
});
