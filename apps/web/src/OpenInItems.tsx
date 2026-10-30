// The Open in list, the same in the pane bar's menu and the agent row's menu: the folder in Finder, VS Code or Terminal, the session in Terminal, and the path.
import { useLoaded } from '@handline/app-data';
import { MenuItem } from '#src/ui/index.ts';
import { showPill } from '#src/Pill.tsx';
import { shell } from '#src/shell.ts';

const copyPath = (path: string) =>
  void navigator.clipboard?.writeText(path).then(
    () => showPill('Path copied'),
    () => showPill('Could not copy the path'),
  );

/** The items for `agent` whose folder is `cwd`; `withCopy` adds Copy path last (a menu that has its own leaves it off). */
export function OpenInItems({ agent, cwd, withCopy = true }: { agent: string; cwd: string; withCopy?: boolean }) {
  const open = shell('openPath');
  const apps = useLoaded(
    ['shell', 'openInApps'],
    async () => {
      const r = await shell('openInApps')!.openInApps!();
      return r.ok ? r.apps : [];
    },
    !!shell('openInApps'),
  );
  const openIn = async (app: 'vscode' | 'terminal' | 'claude') => {
    const r = await shell('openIn')?.openIn!({ app, agent });
    if (r && !r.ok) showPill(r.message);
  };
  return (
    <>
      {open ? (
        <MenuItem icon="open" onSelect={() => void open.openPath!({ path: cwd, agent })}>
          Finder
        </MenuItem>
      ) : null}
      {apps?.includes('claude') ? (
        <MenuItem icon="terminal" onSelect={() => void openIn('claude')}>
          Claude Code in Terminal
        </MenuItem>
      ) : null}
      {apps?.includes('vscode') ? (
        <MenuItem icon="edit" onSelect={() => void openIn('vscode')}>
          VS Code
        </MenuItem>
      ) : null}
      {apps?.includes('terminal') ? (
        <MenuItem icon="terminal" onSelect={() => void openIn('terminal')}>
          Terminal
        </MenuItem>
      ) : null}
      {withCopy ? (
        <MenuItem icon="reference" onSelect={() => copyPath(cwd)}>
          Copy path
        </MenuItem>
      ) : null}
    </>
  );
}
