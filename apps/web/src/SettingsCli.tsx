// The Connection card's row for `hl`: an installed app carries the command and offers to link it into ~/.local/bin.
import { useEffect, useState } from 'react';
import { Button } from '#src/ui/index.ts';
import { Row } from '#src/SettingsRows.tsx';
import { shell } from '#src/shell.ts';

type State = { bundled: boolean; linked: boolean; link: string };

export function CliRow() {
  const [state, setState] = useState<State | null>(null);
  const [error, setError] = useState('');
  const load = () =>
    void shell('cli')
      ?.cli?.()
      .then((r) => r.ok && setState(r));
  useEffect(load, []);
  if (!state?.bundled) return null;
  const link = () =>
    void shell('linkCli')
      ?.linkCli?.()
      .then((r) => (setError(r.ok ? '' : r.message), load()));
  return (
    <Row
      label="Command line"
      hint={error || (state.linked ? `hl is linked at ${state.link}` : 'Put hl on your PATH to use Handline from a terminal')}
    >
      {state.linked ? null : <Button label="Install hl" size="sm" onClick={link} />}
    </Row>
  );
}
