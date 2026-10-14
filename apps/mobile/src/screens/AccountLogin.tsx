// Log an account in by URL and code (docs/pc-app-gaps.md R1), founder only like Remove: account.login returns the URL to open, shown as
// text to copy; the code goes back once through account.login_code and is kept nowhere; Cancel sends account.login_cancel. The login's
// end comes from the accounts topic, not from these writes.
import { useState } from 'react';
import { Text, View } from 'react-native';
import type { Account } from '@handline/client';
import { useStore } from '@handline/app-react';
import { classify, sentence, useServes, useWrite } from '@handline/app-data';
import { space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Button } from './parts.tsx';
import { Input, useWhyNot } from './TaskActions.tsx';

const running = (a: Account) => a.login?.state === 'waiting' || a.login?.state === 'code_sent';

export function AccountLogin({ a }: { a: Account }) {
  const c = usePalette();
  const founder = useStore().client.hello?.role === 'founder';
  const off = useWhyNot('account.login');
  const { served } = useServes('account.login');
  const login = useWrite('account.login', a.name);
  const sendCode = useWrite('account.login_code', a.name);
  const cancel = useWrite('account.login_cancel', a.name);
  const [code, setCode] = useState('');
  if (!founder || !served) return null;
  const run = running(a);
  // Only the client that started the login holds the URL; the topic's login_id is what a Cancel needs after a reload.
  const url = login.data?.url;
  const loginId = login.data?.login_id ?? a.login?.login_id ?? '';
  const submit = () => {
    sendCode.mutate({ login_id: loginId, code: code.trim() });
    setCode('');
  };
  const why = sendCode.error ? sentence(classify(sendCode.error)) : '';
  const stop = (
    <Button testID="login-cancel" label="Cancel" disabled={cancel.isPending} onPress={() => cancel.mutate({ login_id: loginId })} />
  );
  return (
    <View testID="account-login" style={{ gap: space.sm }}>
      {run && url ? (
        <>
          <Text style={{ color: c.muted }}>Open this link in a browser, then bring the code back.</Text>
          <Text testID="login-url" selectable style={{ color: c.ink }}>
            {url}
          </Text>
          <Input testID="login-code" label="Code" value={code} onChange={setCode} />
          {why ? (
            <Text testID="login-error" accessibilityRole="alert" style={{ color: c.block }}>
              {why}
            </Text>
          ) : null}
          <View style={{ flexDirection: 'row', gap: space.sm }}>
            <Button
              testID="login-submit"
              label={sendCode.isPending ? 'Sending…' : 'Submit'}
              disabled={!code.trim() || sendCode.isPending}
              onPress={submit}
              style={{ flex: 1 }}
            />
            {stop}
          </View>
        </>
      ) : run ? (
        <View style={{ gap: space.sm }}>
          <Text testID="login-running" style={{ color: c.muted }}>
            {a.login?.state === 'code_sent' ? 'Code sent; the daemon is finishing the login.' : 'Login in progress.'}
          </Text>
          {stop}
        </View>
      ) : (
        <>
          <Button
            testID="login-open"
            label={login.isPending ? 'Starting…' : a.login_state === 'ok' ? 'Log in again' : 'Log in'}
            disabled={!!off || login.isPending}
            onPress={() => login.mutate({ account: a.name })}
          />
          {off ? <Text style={{ color: c.muted }}>Locked: {off}.</Text> : null}
          {a.login?.state === 'failed' ? (
            <Text testID="login-failed" accessibilityRole="alert" style={{ color: c.block }}>
              Login failed: {a.login.reason}
            </Text>
          ) : null}
        </>
      )}
    </View>
  );
}
