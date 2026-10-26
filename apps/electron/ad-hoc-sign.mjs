// electron-builder's afterSign hook: without a Developer ID the app is signed ad hoc, which Apple Silicon needs before it will run the app at all.
import { execFileSync } from 'node:child_process';
import path from 'node:path';

export default function adHocSign(context) {
  if (process.env.HANDLINE_SIGN_IDENTITY) return;
  const app = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`);
  for (const bin of ['handlined', 'hl']) {
    execFileSync('codesign', ['--force', '--sign', '-', path.join(app, 'Contents', 'Resources', 'bin', bin)], { stdio: 'inherit' });
  }
  execFileSync('codesign', ['--force', '--deep', '--sign', '-', app], { stdio: 'inherit' });
}
