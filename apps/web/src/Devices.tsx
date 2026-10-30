// Settings > Computers: the servers (the computers that run agents; this one is always the first) and the devices that only connect, each with
// when it was last seen and Revoke behind a confirm. Devices always shows, with a way to connect one.
import { useState } from 'react';
import { ago } from '@handline/app-core';
import { useAgents, useDevices, useOff, useRead, useServes, useStatus, useWrite } from '@handline/app-data';
import { useSelector } from '@handline/app-react';
import { Invite } from '#src/Invite.tsx';
import { Row } from '#src/SettingsRows.tsx';
import { shell, useShellMode, useShellNetwork } from '#src/shell.ts';
import { Button, Switch, Dialog, Icon, NOTE, RowHint, RowText, RowTitle, SectionHead, SettingsCard, SettingsRow } from '#src/ui/index.ts';
import { View } from '#src/View.tsx';

/** Desktop app only, and only when it has its own gateway: `run` opens the one-time QR window a phone scans; `error` is why it did not. */
function usePairPhone() {
  const can = !!useShellMode()?.phone && !!shell('phone');
  const [error, setError] = useState('');
  return { can, error, run: () => void shell('phone')!.phone!().then((r) => setError(r.ok ? '' : r.message)) };
}

/** The desktop app only: whether the gateway also serves the page, so a browser on another computer can open an invite. Off until turned on; the app relaunches. */
function AllowNetwork() {
  const net = useShellNetwork();
  if (!net?.can) return null;
  return (
    <SettingsCard>
      <Row label="Allow devices on this network" hint="Another computer opens the invite in a browser. The app restarts when this changes.">
        <Switch label="Allow devices on this network" on={net.on} onChange={(on) => void shell('setNetwork')!.setNetwork!(on)} />
      </Row>
    </SettingsCard>
  );
}

const Tag = ({ children }: { children: string }) => (
  <span className="ml-1 inline-block rounded-full border border-line-strong px-2 text-xs font-normal text-ink-2">{children}</span>
);

/** This computer, the server every daemon has: its name, whether the connection is up, how many agents it runs and its load. */
function ThisComputer() {
  const hello = useSelector((s) => s.client.hello);
  const agents = useAgents().data?.agents.length;
  const load = useStatus().data?.resources?.machine?.load_per_core;
  const host = useRead('connection.get', {}).data?.listener?.tailscale_host?.split('.')[0];
  return (
    <SettingsCard as="ul" className="servers">
      <SettingsRow answer as="li" data-server="this">
        <Icon name="agent" size={16} label="" />
        <RowText>
          <RowTitle plain>
            {host ?? 'This computer'} {host ? <Tag>This computer</Tag> : null} <Tag>Main</Tag>
          </RowTitle>
          <RowHint>
            {hello ? 'Online' : 'Not connected'}
            {agents != null ? ` · ${agents} ${agents === 1 ? 'agent' : 'agents'}` : ''}
            {load != null ? ` · Load ${Math.round(load * 100)}%` : ''}
          </RowHint>
        </RowText>
      </SettingsRow>
    </SettingsCard>
  );
}

export function Devices() {
  const pair = usePairPhone();
  const q = useDevices();
  const { served } = useServes('devices.list');
  const canPair = useServes('device.pair').served;
  const revoke = useWrite('device.revoke');
  const off = useOff('device.revoke');
  const [asking, setAsking] = useState<{ id: string; name: string } | null>(null);
  const [inviting, setInviting] = useState(false);
  const rows = q.data?.devices ?? [];
  return (
    <>
      <SectionHead>Computers</SectionHead>
      <p className={`${NOTE} m-0 mb-3`}>Servers run agents. Devices only connect.</p>
      <h3 className="m-0 mb-1 text-md font-medium">Servers</h3>
      <ThisComputer />
      <div className="mt-5 flex items-end justify-between gap-3">
        <div>
          <h3 className="m-0 text-md font-medium">Devices</h3>
          <p className={`${NOTE} m-0`}>Browsers and phones that connect to this computer. They run no agents.</p>
        </div>
        <span className="flex gap-2">
          {pair.can ? (
            <Button icon="open" label="Pair a phone" onClick={pair.run}>
              Pair a phone
            </Button>
          ) : null}
          {canPair ? (
            <Button label="Connect a device" variant="outline" onClick={() => setInviting(true)}>
              Connect a device
            </Button>
          ) : null}
        </span>
      </div>
      <AllowNetwork />
      {pair.error ? (
        <p className={`${NOTE} text-bad`} role="alert">
          {pair.error}
        </p>
      ) : null}
      <View q={q} served={served} shape="settings" what="devices" empty={!rows.length && 'No devices yet.'}>
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
      {inviting ? <Invite onClose={() => setInviting(false)} /> : null}
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
