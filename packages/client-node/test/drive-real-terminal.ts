// Drives the shared client (the one the web app and the phone use) against a real daemon's gateway and prints what it saw as JSON.
// Run by tests/infra/test_real_terminal.py with HANDLINE_URL, HANDLINE_TOKEN and HANDLINE_AGENT; not a test of its own.
import { DaemonClient, webSocketTransport } from '@handline/client';

const url = process.env.HANDLINE_URL!;
const agent = process.env.HANDLINE_AGENT!;
const seen: Record<string, unknown> = {};

const until = async (cond: () => boolean, what: string, ms = 15000) => {
  const end = Date.now() + ms;
  while (!cond()) {
    if (Date.now() > end) throw new Error(`timed out: ${what}`);
    await new Promise((r) => setTimeout(r, 50));
  }
};

const client = new DaemonClient({ client: 'drive-real-terminal', token: process.env.HANDLINE_TOKEN, reconnect: false, connect: () => webSocketTransport(url) });
try {
  const hello = await client.start();
  seen.formats = hello.capabilities.terminal_format;
  const view = await client.attachTerminal(agent);
  seen.format = view.format;
  seen.size = [view.cols, view.rows];
  seen.attachedLines = view.lines();

  await client.takeInput(agent);
  // 60 numbered lines push the first of them into the scrollback; the last one is painted green.
  const line = "i=0; while [ $i -lt 60 ]; do echo row$i; i=$((i+1)); done; printf 'last\\033[32mgreen\\033[0m\\n'";
  await client.call('terminal.input', client.typedBytes(view.terminal, line, true));
  await until(() => view.text().some((t) => t === 'lastgreen'), 'the painted row reaches the view as a frame');
  seen.afterTyping = view.lines().filter((l) => l.text === 'lastgreen');
  seen.cursorSeq = view.seq;

  await client.loadOlder(view, 30);
  seen.history = { count: view.history.length, first: view.historyFirst, more: view.historyMore, spans: view.historySpans.filter(Boolean).length };

  await client.call('terminal.resize', { terminal: view.terminal, cols: 100, rows: 30 });
  await until(() => view.cols === 100 && view.rows === 30, 'the resize reaches the view');
  seen.resized = [view.cols, view.rows, view.frame.length];
  seen.afterResize = view.lines().filter((l) => l.text === 'lastgreen');

  await client.releaseInput(agent);
  await client.detachTerminal(agent);
  seen.ok = true;
} catch (e) {
  seen.error = String((e as Error).message ?? e);
} finally {
  client.close();
}
console.log(JSON.stringify(seen));
process.exit(0);
