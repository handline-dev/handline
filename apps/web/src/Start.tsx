// Opens the app: a listener that wants a device token gets the pairing screen until this device holds one.
import { useEffect, useMemo, useState } from 'react';
import { Store, type KV } from '@handline/app-core';
import { DaemonClient, webSocketTransport, type Transport } from '@handline/client';
import { App } from './App.tsx';
import { forgetToken, loadToken, needsToken, pairWith, tokenRefused } from './pairing.ts';

type Open = (url: string) => Promise<Transport>;

/** `open` is the socket to the listener; the page's own WebSocket unless a test gives another. */
export function Start({
  url,
  insecure,
  storage,
  open = webSocketTransport,
}: {
  url: string;
  insecure: boolean;
  storage: KV | null;
  open?: Open;
}) {
  const [token, setToken] = useState(() => (needsToken(url) ? loadToken(storage, url) : null));
  const [lost, setLost] = useState(false);
  // A revoked device, or a token from an older pairing: the listener refuses the hello, so the token goes and the pairing screen comes back.
  useEffect(() => {
    if (!token || !needsToken(url)) return;
    let live = true;
    void tokenRefused(url, token, open).then((no) => {
      if (!live || !no) return;
      forgetToken(storage, url);
      setLost(true);
      setToken(null);
    });
    return () => {
      live = false;
    };
  }, [url, token, open, storage]);
  const store = useMemo(() => {
    const s = new Store(new DaemonClient({ client: 'web-pc', token: token ?? undefined, connect: () => open(url) }));
    if (insecure) s.fail(`Refusing ${url}: use wss:// for a daemon that is not on this machine.`);
    return s;
  }, [url, insecure, token, open]);
  if (needsToken(url) && !token && !insecure) return <PairScreen url={url} storage={storage} open={open} lost={lost} onPaired={setToken} />;
  return <App store={store} daemon={url} storage={storage} connect={!insecure} />;
}

function PairScreen(p: { url: string; storage: KV | null; open: Open; lost: boolean; onPaired: (token: string) => void }) {
  const { url, storage, open, lost, onPaired } = p;
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const submit = async () => {
    setBusy(true);
    setError('');
    try {
      onPaired(await pairWith(url, text, storage, open));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };
  return (
    <form
      className="pair-screen"
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim() && !busy) void submit();
      }}
    >
      <h1>Pair this computer</h1>
      {lost && <p role="status">This computer is no longer paired.</p>}
      <p>
        In a terminal run <code>hl device pair</code> and paste the code, or the <code>handline://</code> line, here.
      </p>
      <input autoFocus value={text} onChange={(e) => setText(e.target.value)} placeholder="Pairing code" aria-label="Pairing code" />
      <button type="submit" disabled={busy || !text.trim()}>
        {busy ? 'Pairing…' : 'Pair'}
      </button>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
