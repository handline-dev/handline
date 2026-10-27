import { Profiler, act } from 'react';
import { it } from 'vitest';
import { applySeeds } from '@handline/fake-daemon/seeds';
import { Home } from '#src/screens/Home.tsx';
import { $, ARCH, mount, press, until } from '#test/harness.tsx';

const WHO = 'handline.auditor';
it('perf', async () => {
  let n = 0;
  let ms = 0;
  const on = () => (n = 0, ms = 0);
  const out: string[] = [];
  const m = await mount((s) => void applySeeds(s.daemon, 'chat-long'), undefined, 'founder', {
    screen: (store) => (
      <Profiler id="home" onRender={(_i, _p, d) => ((n += 1), (ms += d))}>
        <Home store={store} onUnpair={() => {}} />
      </Profiler>
    ),
  });
  await until(() => $(`agent-${ARCH}`), 'rows');
  on();
  await press($(`agent-${ARCH}`));
  await until(() => $('chat-input'), 'chat', 20000).catch((e) => { console.log('SCREEN ' + document.body.textContent?.slice(0, 400)); throw e; });
  await act(async () => void (await new Promise((r) => setTimeout(r, 300))));
  out.push(`open chat: ${n} commits, ${ms.toFixed(0)} ms`);
  for (const [label, who] of [['10 msgs to the open chat', ARCH], ['10 msgs to another agent', WHO]] as const) {
    on();
    for (let i = 0; i < 10; i++) {
      await act(async () => void m.daemon.chat.ask(who, `ping ${label} ${i}`, 'founder'));
    }
    await act(async () => void (await new Promise((r) => setTimeout(r, 200))));
    out.push(`${label}: ${n} commits, ${ms.toFixed(0)} ms`);
  }
  console.log('PERF ' + (process.env.PERF_TAG ?? '') + '\n' + out.join('\n'));
  m.store.stop();
});
