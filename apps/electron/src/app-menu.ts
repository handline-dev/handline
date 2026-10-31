// The application menu as plain data, so a test can read it; main.ts hands it to Electron's Menu.
import type { MenuItemConstructorOptions } from 'electron';

/** The screens the Go menu opens, with the page route each one is (routeOfTarget in app-core). */
export const GO_SCREENS = [
  { label: 'Agents', screen: 'team', key: '1' },
  { label: 'Requests', screen: 'requests', key: '2' },
  { label: 'Tasks', screen: 'tasks', key: '3' },
  { label: 'Usage', screen: 'stats', key: '4' },
] as const;

export interface MenuActions {
  /** Opens the window if it is closed, then shows this screen. */
  go(screen: string): void;
  /** Where the app is a Mac app: the name leads the menu bar. */
  mac: boolean;
}

export function appMenu(a: MenuActions): MenuItemConstructorOptions[] {
  const settings: MenuItemConstructorOptions = { label: 'Settings…', accelerator: 'CmdOrCtrl+,', click: () => a.go('settings') };
  const go: MenuItemConstructorOptions = {
    label: 'Go',
    submenu: GO_SCREENS.map((s) => ({ label: s.label, accelerator: `CmdOrCtrl+${s.key}`, click: () => a.go(s.screen) })),
  };
  return [
    ...(a.mac
      ? [
          {
            label: 'Handline',
            submenu: [
              { role: 'about' },
              { type: 'separator' },
              settings,
              { type: 'separator' },
              { role: 'services' },
              { type: 'separator' },
              { role: 'hide' },
              { role: 'hideOthers' },
              { role: 'unhide' },
              { type: 'separator' },
              { role: 'quit' },
            ],
          } satisfies MenuItemConstructorOptions,
        ]
      : [{ label: 'File', submenu: [settings, { type: 'separator' }, { role: 'quit' }] } satisfies MenuItemConstructorOptions]),
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        { role: 'selectAll' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
    go,
    { label: 'Window', submenu: [{ role: 'minimize' }, { role: 'zoom' }, { role: 'close' }] },
  ];
}
