// The invite window ("Connect a device"): the daemon makes a one-time code, the window shows it as a QR and a link for 5 minutes, and closes with a
// notice when a device pairs. The link is only on this screen; nothing keeps it.
import qrcode from 'qrcode-generator';
import { useEffect, useMemo, useState } from 'react';
import { inviteLink } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import { useDevices, useRead, useWrite } from '@handline/app-data';
import { Button, Dialog, Icon, NOTE } from '#src/ui/index.ts';

type Code = { link: string | null; until: number };

/** The link as an SVG QR; it is drawn on white so a scanner reads it in the dark theme too. */
function Qr({ link }: { link: string }) {
  const svg = useMemo(() => {
    const q = qrcode(0, 'M');
    q.addData(link);
    q.make();
    return q.createSvgTag({ cellSize: 6, margin: 0, scalable: true });
  }, [link]);
  return (
    <div
      role="img"
      aria-label="Invite QR code"
      className="size-[156px] shrink-0 rounded-md bg-white p-2.5 [&>svg]:size-full"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}

const clock = (ms: number) => `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}`;

export function Invite({ onClose }: { onClose: () => void }) {
  const store = useStore();
  const pair = useWrite('device.pair');
  const known = useDevices().data?.devices;
  const encrypted = useRead('connection.get', {}).data?.listener?.encryption === 'tls';
  const [start] = useState(() => new Set(known?.map((d) => d.id)));
  const [code, setCode] = useState<Code | null>(null);
  const [left, setLeft] = useState(0);
  const [copied, setCopied] = useState(false);
  const make = () => {
    setCopied(false);
    pair.mutate(
      {},
      {
        onSuccess: (r) => {
          setCode({ link: inviteLink(r.uri, encrypted), until: Date.parse(r.expires_at) });
        },
      },
    );
  };
  useEffect(make, []); // eslint-disable-line react-hooks/exhaustive-deps -- one code when the window opens
  useEffect(() => {
    if (!code) return;
    const tick = () => setLeft(Math.max(0, code.until - Date.now()));
    tick();
    const t = setInterval(tick, 500);
    return () => clearInterval(t);
  }, [code]);
  const joined = known?.find((d) => !start.has(d.id));
  useEffect(() => {
    if (!joined) return;
    store.notices.push('ok', `${joined.name} connected`);
    onClose();
  }, [joined]); // eslint-disable-line react-hooks/exhaustive-deps -- once per new device
  const link = code?.link ?? null;
  const expired = !!code && left === 0;
  return (
    <Dialog title="Connect a device" onClose={onClose}>
      {link && !expired ? (
        <div className="flex flex-wrap items-start gap-5">
          <Qr link={link} />
          <div className="flex min-w-[220px] flex-1 flex-col gap-2">
            <span className="text-sm text-ink-2">Or open this on the other computer</span>
            <code className="block rounded-md bg-panel px-3 py-2 font-mono text-sm [overflow-wrap:anywhere]">{link}</code>
            <div className="flex items-center justify-between gap-3">
              <span className="flex items-center gap-1.5 text-sm text-ink-2">
                <Icon name="waiting" size={14} label="" /> Valid for <b className="tabular-nums text-ink">{clock(left)}</b>
              </span>
              <Button
                label="Copy link"
                variant="outline"
                onClick={() => void navigator.clipboard?.writeText(link).then(() => setCopied(true))}
              >
                {copied ? 'Copied' : 'Copy link'}
              </Button>
            </div>
          </div>
        </div>
      ) : (
        <div className="flex flex-col items-start gap-2">
          <p className="m-0" role={expired ? 'status' : undefined}>
            {expired
              ? 'This invite expired.'
              : code
                ? 'Another computer cannot reach this one: the app listens on this computer only. Turn on Tailscale, or Allow devices on this network, and restart.'
                : pair.isError
                  ? 'No invite could be made.'
                  : 'Making an invite…'}
          </p>
          {expired || pair.isError ? (
            <Button label="New invite" variant="solid" onClick={make}>
              New invite
            </Button>
          ) : null}
        </div>
      )}
      <p className={`${NOTE} m-0 border-t border-line pt-3`}>
        Anyone with this link for 5 minutes gets full control of this computer&apos;s agents. It works once.
      </p>
    </Dialog>
  );
}
