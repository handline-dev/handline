import path from 'node:path';

const repoRoot = path.resolve(import.meta.dirname, '../../..');

const REACT = ['react', 'react-dom', 'react-native'].map((name) => ({ name, message: 'This package must not depend on React.' }));
const RELATIVE = { regex: '^\\.', message: 'Import by package name, or by #path inside this package.' };
const DEEP = { group: ['@handline/*/src/*'], message: 'Import the package entry, not its src.' };
const QUERY = { group: ['@tanstack/react-query'], message: 'React Query lives in packages/app-data.' };
const NO_APPS = { group: ['@handline/web', '@handline/mobile', '@handline/electron'], message: 'An app is never imported.' };
const NO_APP_PKGS = { group: ['@handline/app-*'], message: 'This package sits below the app packages.' };
const NO_FAKE = {
  group: ['@handline/fake-daemon', '@handline/fake-daemon/*'],
  message: 'The fake daemon is for tests, dev scripts and vite.config.ts only.',
};

// no-restricted-imports sees only static imports; this refuses a relative path in import(), require() and vi.mock() too.
const RELATIVE_CALL = {
  selector: [
    'ImportExpression[source.value=/^\\./]',
    "CallExpression[callee.name='require'][arguments.0.value=/^\\./]",
    'CallExpression[callee.property.name=/^(mock|doMock|importActual)$/][arguments.0.value=/^\\./]',
  ].join(', '),
  message: RELATIVE.message,
};

/** What the folder at `where` (relative to the repo root) may not import, from docs/turborepo-layout.md. */
function restrictions(where) {
  const [top, name] = where.split(path.sep);
  if (top === 'apps')
    return { paths: [], patterns: [RELATIVE, DEEP, QUERY, NO_APPS, NO_FAKE], exempt: ['test/**', 'scripts/**', 'vite.config.ts'] };
  if (top !== 'packages') return { paths: [], patterns: [RELATIVE, DEEP, QUERY], exempt: [] };
  const patterns = name === 'app-data' ? [RELATIVE, DEEP, NO_APPS] : [RELATIVE, DEEP, QUERY, NO_APPS];
  if (name === 'client' || name === 'tokens') return { paths: REACT, patterns: [...patterns, NO_APP_PKGS], exempt: [] };
  if (name === 'app-core') return { paths: REACT, patterns, exempt: [] };
  return { paths: [], patterns, exempt: [] };
}

/**
 * The import boundaries for the package whose config calls this. A rule's options are replaced, not merged, and
 * `files` globs are relative to the package, so the folder is worked out here from `dir` and not matched by glob.
 */
export function boundaries(dir) {
  const { paths, patterns, exempt } = restrictions(path.relative(repoRoot, dir));
  const rule = (list) => ({
    'no-restricted-imports': ['error', { paths, patterns: list }],
    'no-restricted-syntax': ['error', RELATIVE_CALL],
  });
  const exemptList = patterns.filter((p) => p !== NO_FAKE);
  return [{ rules: rule(patterns) }, ...(exempt.length ? [{ files: exempt, rules: rule(exemptList) }] : [])];
}
