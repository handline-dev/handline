// Shown above the page while the daemon is not reachable: says so, and what to do about it.
import { useState } from 'react';
import { useConnection } from '@handline/app-react';
import { Button, Icon } from '#src/ui/index.ts';

const START = 'hl service install';

export function DaemonDown() {
  const conn = useConnection();
  const [copied, setCopied] = useState(false);
  if (conn !== 'closed' && conn !== 'reconnecting') return null;
  return (
    <section
      className="daemon-down m-2 flex items-start gap-2.5 h-fit rounded-md border-2 border-bad bg-panel px-3 py-2.5"
      role="alert"
      aria-label="Handline is not running"
    >
      <Icon name="offline" size={18} aria-hidden />
      <div>
        <strong>Handline isn&apos;t running</strong>
        <p className="muted m-0 mt-0.5">
          {conn === 'reconnecting' ? 'Reconnecting… ' : ''}What you see may be out of date. Run this in Terminal on the host to start it;
          this page reconnects by itself.
        </p>
        <div className="mt-1.5 flex items-center gap-2">
          <code>{START}</code>
          <Button
            variant="outline"
            size="sm"
            label={copied ? 'Copied' : 'Copy command'}
            onClick={() =>
              void navigator.clipboard?.writeText(START).then(
                () => setCopied(true),
                () => {},
              )
            }
          >
            {copied ? 'Copied' : 'Copy command'}
          </Button>
        </div>
      </div>
    </section>
  );
}
