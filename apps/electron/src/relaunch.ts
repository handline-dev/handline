/**
 * The command that starts the app again once the running one has quit. It polls for the old process, not once: it is a detached child, so it has nothing to wait on
 * (and the new app would meet the old one's single-instance lock). A window already open on an old-style bundle keeps showing what it has until it is restarted.
 * It runs launch.ts, so the Handline bundle of a new Electron version is made and used.
 */
export function relaunchCommand(pid: number, electronDir: string): { file: string; args: string[] } {
  return {
    file: 'sh',
    args: ['-c', 'while kill -0 "$1" 2>/dev/null; do sleep 0.2; done; cd "$2" && exec node launch.ts', 'sh', String(pid), electronDir],
  };
}
