// Opens the app: a listener that wants a device token gets the pairing screen until this device holds one.
import { useEffect, useMemo, useState } from 'react';
import { Store, type KV } from '@handline/app-core';
import { DaemonClient, webSocketTransport, type Transport } from '@handline/client';
import { App } from './App.tsx';
import { shell } from './shell.ts';
import { forgetToken, loadToken, needsToken, pairWith, tokenRefused } from './pairing.ts';

type Open = (url: string) => Promise<Transport>;

/** `open` is the socket to the listener; the page's own WebSocket unless a test gives another. */
export function Start({
  url,
  insecure,
  failure = null,
  storage,
  open = webSocketTransport,
}: {
  url: string;
  insecure: boolean;
  /** Why the desktop shell's own daemon is not up, when it is not: shown in place of a connection that cannot work. */
  failure?: string | null;
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
    if (failure) s.fail(failure);
    else if (insecure) s.fail(`Refusing ${url}: use wss:// for a daemon that is not on this machine.`);
    return s;
  }, [url, insecure, failure, token, open]);
  if (needsToken(url) && !token && !insecure && !failure)
    return <PairScreen url={url} storage={storage} open={open} lost={lost} onPaired={setToken} />;
  return <App store={store} daemon={url} storage={storage} connect={!insecure && !failure} />;
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
      <Connection />
    </form>
  );
}

/** In the desktop app: which daemon it is on, the switch to the other mode, and pairing with another device by address and code. */
function Connection() {
  const [now, setNow] = useState<{ mode: 'connect' | 'own'; remote: string | null } | null>(null);
  const [address, setAddress] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    void shell('mode')
      ?.mode?.()
      .then((r) => r.ok && setNow({ mode: r.mode, remote: r.remote }));
  }, []);
  if (!now) return null;
  const run = async (f: () => Promise<{ ok: true } | { ok: false; message: string }>) => {
    setBusy(true);
    setError('');
    const r = await f();
    if (!r.ok) {
      setError(r.message);
      setBusy(false);
    }
  };
  const other = now.mode === 'connect' ? 'own' : 'connect';
  return (
    <fieldset className="connection" disabled={busy}>
      <legend>Which daemon</legend>
      <p>
        {now.mode === 'own'
          ? 'This app runs its own daemon.'
          : now.remote
            ? `Connect mode, paired with ${now.remote}.`
            : 'Connect mode: using the daemon already running here.'}
      </p>
      <button type="button" onClick={() => void run(() => shell('setMode')!.setMode!(other))}>
        {other === 'own' ? 'Run its own daemon' : 'Connect to a daemon'}
      </button>
      {now.mode === 'connect' && (
        <>
          <p>
            Or pair with another device: its address and the code from <code>hl device pair</code>.
          </p>
          <input value={address} onChange={(e) => setAddress(e.target.value)} placeholder="Address (name.ts.net)" aria-label="Address" />
          <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="Code" aria-label="Device code" />
          <button
            type="button"
            disabled={!address.trim() || !code.trim()}
            onClick={() => void run(() => shell('pairRemote')!.pairRemote!({ address, code }))}
          >
            Pair with this device
          </button>
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </fieldset>
  );
}
