// The files side of an installed app's update, with no Electron in it: the download, its checksum and the unpacking (pkg-update-app.ts wires them in).
import { execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

/** Saves `url` into `dir`, calling `progress` with 0 to 1 as it arrives; returns the file. Rejects on a status that is not 200. */
export async function downloadTo(dir: string, url: string, progress: (fraction: number) => void): Promise<string> {
  const r = await fetch(url);
  if (!r.ok || !r.body) throw new Error(`${url}: ${r.status}`);
  const total = Number(r.headers.get('content-length')) || 0;
  const file = path.join(dir, `handline-update-${randomUUID()}.zip`);
  const out = fs.createWriteStream(file);
  let got = 0;
  for await (const chunk of r.body as unknown as AsyncIterable<Uint8Array>) {
    got += chunk.length;
    if (!out.write(chunk)) await new Promise((res) => out.once('drain', () => res(null)));
    if (total) progress(got / total);
  }
  await new Promise((res, rej) => out.end((e?: Error | null) => (e ? rej(e) : res(null))));
  return file;
}

/** The SHA-512 of a file, base64: the form latest-mac.yml has. */
export function sha512Of(file: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const h = createHash('sha512');
    fs.createReadStream(file)
      .on('data', (d) => h.update(d))
      .on('error', reject)
      .on('end', () => resolve(h.digest('base64')));
  });
}

/** Unpacks the zip (made by ditto, as electron-builder does) into `staging`, and returns `<staging>/<app name>`; rejects when the app is not in it. */
export async function unpackApp(zip: string, staging: string, appName: string): Promise<string> {
  fs.rmSync(staging, { recursive: true, force: true });
  await new Promise<void>((resolve, reject) =>
    execFile('ditto', ['-xk', zip, staging], (err, _o, errOut) =>
      err ? reject(new Error(`ditto: ${(errOut || err.message).trim().split('\n').pop()}`)) : resolve(),
    ),
  );
  fs.rmSync(zip, { force: true });
  const app = path.join(staging, appName);
  if (!fs.existsSync(path.join(app, 'Contents', 'MacOS'))) throw new Error('the download has no app in it');
  return app;
}
