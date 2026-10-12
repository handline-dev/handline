// The login by URL and code: account.login starts the vendor's CLI without a terminal, the page shows the URL it printed and hands the
// code back with account.login_code; account.login_cancel stops it. The code is never kept — it lives in its box alone and is cleared
// on submit. The login's end comes from the accounts topic, not from these writes.
import { useState } from 'react';
import type { Account } from '@handline/client';
import { useConnection } from '@handline/app-react';
import { classify, sentence, useServes, useWrite } from '@handline/app-data';
import { Button, Field } from './ui/index.ts';

/** A login the daemon has not ended: the URL (when this app started it) and the code box belong to it. */
const running = (account: Account) => account.login?.state === 'waiting' || account.login?.state === 'code_sent';

export function AccountLogin({ account }: { account: Account }) {
  const login = useWrite('account.login', account.name);
  const sendCode = useWrite('account.login_code', account.name);
  const cancelLogin = useWrite('account.login_cancel', account.name);
  const { served } = useServes('account.login');
  const open = useConnection() === 'open';
  const [code, setCode] = useState('');

  const off = !open ? 'daemon not connected' : served ? undefined : 'this daemon does not serve account.login';
  const run = running(account);
  // The URL only the starter of the login holds; a reload loses it and the topic's login_id is what a Cancel still needs.
  const url = login.data?.url;
  const loginId = login.data?.login_id ?? account.login?.login_id ?? '';
  const submit = () => {
    sendCode.mutate({ login_id: loginId, code: code.trim() });
    setCode('');
  };
  const why = sendCode.error ? sentence(classify(sendCode.error)) : '';
  return (
    <div className="acct-login flex flex-col gap-2">
      <span className="muted phone-only hidden max-sm:inline">Log in from a computer.</span>
      <div className="desktop-only flex max-sm:hidden flex-col gap-2">
        {run && url ? (
          <>
            <p className="muted m-0 flex flex-wrap items-center gap-2">
              Open
              <a className="acct-login-url mono" href={url} target="_blank" rel="noreferrer">
                {url}
              </a>
              <Button variant="outline" size="sm" label="Copy" title="Copy the link" onClick={() => void navigator.clipboard?.writeText(url)} />
              and bring the code back.
            </p>
            <div className="flex items-end gap-2">
              <Field
                label="Code"
                value={code}
                disabled={sendCode.isPending}
                onChange={(e) => setCode(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && !!code.trim() && submit()}
              />
              <Button variant="solid" label={sendCode.isPending ? '…' : 'Submit'} disabled={!code.trim() || sendCode.isPending} onClick={submit} />
              <Button variant="ghost" label="Cancel" disabled={cancelLogin.isPending} onClick={() => cancelLogin.mutate({ login_id: loginId })} />
            </div>
            {why ? (
              <p className="muted m-0" role="alert">
                {why}
              </p>
            ) : null}
          </>
        ) : run ? (
          // A login another client started (or one a reload took the URL away from) still runs: cancel it, then Log in again.
          <p className="muted m-0 flex items-center gap-2" role="status">
            {account.login?.state === 'code_sent' ? 'Code sent; the daemon is finishing the login.' : 'Login in progress.'}
            <Button variant="ghost" size="sm" label="Cancel" disabled={cancelLogin.isPending} onClick={() => cancelLogin.mutate({ login_id: loginId })} />
          </p>
        ) : (
          <div className="flex items-center gap-2">
            <Button
              label={login.isPending ? '…' : account.login_state === 'ok' ? 'Log in again' : 'Log in'}
              disabled={!!off || login.isPending}
              title={off ?? `Or from the host: hl login ${account.name}`}
              onClick={() => login.mutate({ account: account.name })}
            />
            {account.login?.state === 'failed' ? (
              <p className="muted m-0" role="alert">
                Login failed: {account.login.reason}
              </p>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}
