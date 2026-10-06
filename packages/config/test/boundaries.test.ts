import { test } from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { ESLint } from 'eslint';
import tseslint from 'typescript-eslint';

const repo = path.resolve(import.meta.dirname, '../../..');

/**
 * The rule ids ESLint reports for `code` as if it sat at `file` (repo-relative), under that package's own config.
 * The file does not exist, so type information is off; the rules under test do not need it.
 */
async function lint(file: string, code: string): Promise<string[]> {
  const pkg = file.split('/').slice(0, 2).join('/');
  const eslint = new ESLint({
    cwd: path.join(repo, pkg),
    overrideConfig: [
      tseslint.configs.disableTypeChecked,
      { languageOptions: { parserOptions: { projectService: false, tsconfigRootDir: path.join(repo, pkg) } } },
    ],
  });
  const [result] = await eslint.lintText(code, { filePath: path.join(repo, file) });
  return result!.messages.map((m) => m.ruleId ?? m.message.slice(0, 200));
}

const restricted = 'no-restricted-imports';
const bad: [string, string, string][] = [
  ['React in the client', 'packages/client/src/zz.ts', "import 'react';\n"],
  ['an app package below the apps', 'packages/client/src/zz.ts', "import '@handline/app-core';\n"],
  ['React in app-core', 'packages/app-core/src/zz.ts', "import 'react-dom';\n"],
  ['a deep import', 'packages/app-core/src/zz.ts', "import '@handline/client/src/client.ts';\n"],
  ['an app from a package', 'packages/app-react/src/zz.ts', "import '../../../apps/web/src/zz.ts';\n"],
  ['React Query outside app-data', 'packages/app-react/src/zz.ts', "import '@tanstack/react-query';\n"],
  ['React Query in an app', 'apps/web/src/zz.ts', "import '@tanstack/react-query';\n"],
  ['the fake daemon in app source', 'apps/web/src/zz.ts', "import '@handline/fake-daemon/daemon';\n"],
  ['one app from another', 'apps/mobile/src/zz.ts', "import '@handline/web';\n"],
];
for (const [what, file, code] of bad) {
  test(`${what} is refused`, async () => {
    const ids = await lint(file, code);
    assert.ok(ids.includes(restricted), ids.join('; '));
  });
}

const allowed: [string, string, string][] = [
  ['the fake daemon in an app test', 'apps/web/test/zz.ts', "import '@handline/fake-daemon';\n"],
  ['the fake daemon in a dev script', 'apps/mobile/scripts/zz.ts', "import '@handline/fake-daemon';\n"],
  ['React in an app', 'apps/web/src/zz.ts', "import 'react';\n"],
];
for (const [what, file, code] of allowed) {
  test(`${what} is allowed`, async () => {
    const ids = await lint(file, code);
    assert.ok(!ids.includes(restricted), ids.join('; '));
  });
}

test('a file over the limit and a function over the limit are refused', async () => {
  const ids = await lint(
    'packages/client/src/zz.ts',
    `${'export const a = 1;\n'.repeat(401)}export function f() {\n${'  void 0;\n'.repeat(121)}}\n`,
  );
  assert.ok(ids.includes('max-lines') && ids.includes('max-lines-per-function'));
});
