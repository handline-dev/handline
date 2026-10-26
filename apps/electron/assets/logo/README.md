# The Handline mark

One node with three lines out of it, on the app's primary blue (`#4a6fc4`). These SVGs are the sources; the app images are renders of them.

| Source | Rendered to |
| --- | --- |
| `tile.svg` (bolder strokes in `apps/web/public/favicon.svg`) | web favicon, `apps/mobile/assets/favicon.png` (48 px) |
| `square.svg` | `apps/mobile/assets/icon.png` (1024 px, no transparency: the OS rounds it) |
| `mac.svg` | `apps/electron/assets/icon.png` (1024 px, Apple's 824 px grid) |
| `tray.svg` | `apps/electron/assets/trayTemplate.png` (18 px) and `@2x` (36 px) |
| `android-fg.svg`, `android-mono.svg` | `apps/mobile/assets/android-icon-foreground.png` (512 px), `-monochrome.png` (432 px); the background is plain blue |
| `splash.svg` | `apps/mobile/assets/splash-icon.png` (1024 px) |
