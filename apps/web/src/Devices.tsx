// The paired devices: one row each with when it was last seen, and Revoke behind a confirm (the person loses that device's access at once).
import { useEffect, useState } from 'react';
import { ago } from '@handline/app-core';
import { useDevices, useServes, useWrite } from '@handline/app-data';
import { shell } from '#src/shell.ts';
import { Button, Dialog, Icon, NOTE, RowTitle, SectionHead, SettingsCard, SettingsRow } from '#src/ui/index.ts';
import { useOff } from '#src/useOff.ts';
import { View } from '#src/View.tsx';

/** Desktop app only, and only when it has its own gateway: `run` opens the one-time QR window a phone scans; `error` is why it did not. */
function usePairPhone() {
  const [can, setCan] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    void shell('mode')
      ?.mode?.()
      .then((r) => setCan(r.ok && r.phone && !!shell('phone')));
  }, []);
  return { can, error, run: () => void shell('phone')!.phone!().then((r) => setError(r.ok ? '' : r.message)) };
}

export function Devices() {
  const pair = usePairPhone();
  const q = useDevices();
  const { served } = useServes('devices.list');
  const revoke = useWrite('device.revoke');
  const off = useOff('device.revoke');
  const [asking, setAsking] = useState<{ id: string; name: string } | null>(null);
  const rows = q.data?.devices ?? [];
  return (
    <>
      <SectionHead>Paired devices</SectionHead>
      {pair.can ? (
        <p className="m-0 mb-2">
          <Button icon="open" label="Pair a phone" onClick={pair.run}>
            Pair a phone
          </Button>
        </p>
      ) : null}
      {pair.error ? (
        <p className={`${NOTE} text-bad`} role="alert">
          {pair.error}
        </p>
      ) : null}
      <View q={q} served={served} shape="settings" what="paired devices" empty={!rows.length && 'No device is paired.'}>
        <SettingsCard as="ul" className="devices">
          {rows.map((d) => (
            <SettingsRow answer as="li" key={d.id} data-device={d.id}>
              <Icon name={d.connected ? 'agent' : 'waiting'} size={16} label={d.connected ? 'Connected' : 'Not connected'} />
              <RowTitle plain className="[overflow-wrap:anywhere]">
                {d.name} <span className="muted">{d.connected ? 'connected' : d.last_seen ? `seen ${ago(d.last_seen)} ago` : 'never seen'}</span>
              </RowTitle>
              <Button icon="dismiss" label={`Revoke ${d.name}`} disabled={!!off} title={off} onClick={() => setAsking({ id: d.id, name: d.name })} />
            </SettingsRow>
          ))}
        </SettingsCard>
      </View>
      {asking ? (
        <Dialog
          title={`Revoke ${asking.name}?`}
          onClose={() => setAsking(null)}
          action={{
            label: 'Revoke',
            onClick: () => {
              revoke.mutate({ device: asking.id });
              setAsking(null);
            },
          }}
        >
          <p className="m-0">{asking.name} loses its access to this daemon at once. To use it again it has to be paired anew.</p>
          <div>
            <Button label="Cancel" onClick={() => setAsking(null)} />
          </div>
        </Dialog>
      ) : null}
    </>
  );
}
