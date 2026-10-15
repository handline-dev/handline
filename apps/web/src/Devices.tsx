// The paired devices: one row each with when it was last seen, and Revoke behind a confirm (the person loses that device's access at once).
import { useState } from 'react';
import { ago } from '@handline/app-core';
import { useDevices, useServes, useWrite } from '@handline/app-data';
import { Button, Dialog, Icon, RowTitle, SectionHead, SettingsCard, SettingsRow } from './ui/index.ts';
import { useOff } from './useOff.ts';
import { View } from './View.tsx';

export function Devices() {
  const q = useDevices();
  const { served } = useServes('devices.list');
  const revoke = useWrite('device.revoke');
  const off = useOff('device.revoke');
  const [asking, setAsking] = useState<{ id: string; name: string } | null>(null);
  const rows = q.data?.devices ?? [];
  return (
    <>
      <SectionHead>Paired devices</SectionHead>
      <View q={q} served={served} shape="settings" what="paired devices" empty={!rows.length && 'No device is paired.'}>
        <SettingsCard as="ul" className="devices">
          {rows.map((d) => (
            <SettingsRow as="li" key={d.id} data-device={d.id}>
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
        </Dialog>
      ) : null}
    </>
  );
}
