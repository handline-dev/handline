# Lint, format and file size limits for the TypeScript monorepo

Founder asks: should a per-file length limit force code to be split, and should lint tooling come in too. This recommends one rule set, one tool
setup and how to adopt it. Measured on main merged into this branch. Docs only: nothing here is installed yet.

## 1. Recommendation in one screen

| Question | Answer |
|---|---|
| Metric | **Lines** of code per file, blank lines and comment-only lines not counted. Not characters, not tokens (§3). |
| File limit, source | error at **400**; aim for 250 (a guide in `AGENTS.md`, not a rule) |
| File limit, tests | error at **600** |
| Function limit | error at **120** lines per function; aim for 60 (React components 250, see §5) |
| Tool | **ESLint 10 flat config + typescript-eslint** for lint, **Prettier** for format. oxlint can be added later as a fast first pass. Not Biome (§4). |
| Shared config | `packages/config` exports `base`, `react`, `test` flat-config arrays; each package has a two-line `eslint.config.js` (§6) |
| Enforced where | a turbo `lint` task, run by the merge gate next to `test` and `typecheck`. The gate is the only hard stop; agents run the same task before asking for review (§7). |
| Exemptions | generated code, `contract/fixtures`, lockfiles, `dist`; and a `max-lines` disable comment that must carry a reason (§5) |
| Adoption | the repo is small enough to **fix all now**; no baseline file except for what cannot be fixed in the same change (§8) |
| Today's damage | one source file over the error line, three over 300; plan in §9 |

## 2. What others do

- **ESLint `max-lines`** defaults to 300 lines, has `skipBlankLines` and `skipComments`, and says industry advice is 100-500 lines.
  `max-lines-per-function` defaults to 50 with the same skips. [max-lines](https://eslint.org/docs/latest/rules/max-lines),
  [max-lines-per-function](https://eslint.org/docs/rules/max-lines-per-function)
- **Biome** has `noExcessiveLinesPerFunction` (default 50, since 2.0) and `noExcessiveLinesPerFile` (default 300, `skipBlankLines`, not in the recommended
  set, since 2.3). The function limit's ceiling was raised to 65535 in 2.2.7 so large JSX components could be configured.
  [per function](https://biomejs.dev/linter/rules/no-excessive-lines-per-function), [per file](https://biomejs.dev/linter/rules/no-excessive-lines-per-file)
- **oxlint** ships `max-lines` as a pedantic rule, so it is off until enabled. [max-lines in oxlint](https://oxc.rs/docs/guide/usage/linter/rules/eslint/max-lines)
- The common default across the three is **300 lines per file and 50 per function**. Teams that adopt it on real React code loosen the function limit
  (components are long JSX) and relax tests, which is what §1 does.
- **Exemptions** are by path (`ignores` in flat config, `overrides` in Biome) for generated code and fixtures, and by a file-level disable comment
  for the rest. Biome: `biome-ignore-all ... <reason>`; ESLint: `/* eslint-disable max-lines -- reason */`.
- **Characters or tokens** are not offered by any of these tools. Lines are the unit every linter counts and every reviewer sees in a diff.

## 3. Why lines, and what the evidence says about agents

Honest summary: the direct evidence that smaller files make agents *edit more accurately* is thin. What is solid is cost, truncation and blast radius.

- **Context length hurts by itself.** Accuracy drops 13.9% to 85% as input grows even when the model retrieves everything it needs.
  [Context Length Alone Hurts LLM Performance Despite Perfect Retrieval](https://arxiv.org/html/2510.05381v1). Long-context code benchmarks show the same
  (one drop from 29% to 3%): [LongCodeBench](https://arxiv.org/html/2505.07897v1). Neither measures file size inside a repo; they measure prompt length.
  A 2000-line file is read whole into the prompt on every edit round, so it is the same effect in practice.
- **Harnesses truncate.** Coding agents read files in capped slices: this harness reads 2000 lines by default, and other agents cap at 2000 lines or 50 KB
  ([one write-up of such caps](https://pickuma.com/for-dev/context-windows-ai-coding-agents/); secondary source, treat the exact numbers as indicative).
  A file past the cap needs several reads and the agent edits what it has not fully seen.
- **Practitioner advice** says 150-500 lines is the sweet spot for AI editing ([blog](https://medium.com/@eamonn.faherty_58176/right-sizing-your-python-files-the-150-500-line-sweet-spot-for-ai-code-editors-340d550dcea4)).
  This is opinion, not measurement; it agrees with the ESLint default and is the only reason to pick 400 rather than 1000.
- **Parallel agents** matter more for us than accuracy. Several agents work on branches of one repo; two agents editing one 900-line file conflict, two
  agents editing two 200-line files do not. This is our own reasoning, not a cited result.

Lines against characters: our TypeScript averages 50 characters per line (largest file: 24,287 characters in 486 lines), so 400 lines is about 20 KB,
about 5-6k tokens. A character limit would punish long type unions and reward cramming; a line limit plus Prettier's print width (§6) bounds both.
Tokens need a tokenizer in CI for no gain.

## 4. Tool choice

| | ESLint 10 + typescript-eslint + Prettier | Biome | oxlint |
|---|---|---|---|
| File and function size rules | yes, built in | yes, both | `max-lines` yes; check function rule at adoption |
| React hooks rules | `eslint-plugin-react-hooks`, the maintained source | partial re-implementation | via a JS plugin bridge |
| `@tanstack/eslint-plugin-query` (three rules: `exhaustive-deps`, `no-rest-destructuring`, `stable-query-client`) | yes, flat config preset `flat/recommended` ([docs](https://tanstack.com/query/latest/docs/eslint/eslint-plugin-query)) | none | a port exists, version 0.1: too new |
| Type-aware rules (`no-floating-promises` matters for our async client) | yes, most mature | limited | in progress |
| Import boundaries by folder | `no-restricted-imports` with patterns, or `eslint-plugin-boundaries` | `noRestrictedImports`, less per-path control | yes for plain restrictions |
| Format | Prettier (separate tool) | built in, Prettier-compatible | none |
| Speed on 4.4k lines of TS | irrelevant | irrelevant | irrelevant |

Why ESLint: the two things the founder named, the query checklist and the hooks rules, exist only as ESLint plugins, and type-aware rules catch real
bugs in an async client. Biome would be one tool instead of two, which is its real appeal; it loses on the plugins. Speed is the usual reason to go
native, and at this size lint takes seconds either way. The 2026 pattern at large repos is oxlint as a fast pre-pass in front of ESLint
([comparison](https://dev.to/royce_fabbd83cb268312e928/biome-vs-eslint-vs-oxlint-js-linters-2026-408h), a blog, not a benchmark we ran);
we can add it when lint time hurts and change nothing else.

Prettier over Biome format: Biome format is close to Prettier but not identical, and mixing ESLint with a different formatter means turning off the
ESLint style rules anyway (`eslint-config-prettier`, or simply not enabling any style rules; typescript-eslint's `recommended` has none).

Version note: ESLint 10 (February 2026) makes flat config the only format and ESLint 9 left support in August 2026, so start on 10. I could not
check the npm registry from this sandbox, so **verify at install that typescript-eslint, `eslint-plugin-react-hooks` and the query plugin list ESLint 10
in their peer ranges**; if one does not, pin ESLint 9 for a month rather than disabling the plugin.

## 5. Rule set

Base (all packages and apps):

| Rule | Level | Setting |
|---|---|---|
| `max-lines` | error 400 | `skipBlankLines`, `skipComments` |
| `max-lines-per-function` | error 120 | `skipBlankLines`, `skipComments`, `IIFEs: true` |
| `max-depth` | error | 4 |
| typescript-eslint `recommended` | error | non-type-aware, fast |
| `@typescript-eslint/no-floating-promises`, `no-misused-promises` | error | the only type-aware rules at first (`projectService: true`) |
| `@typescript-eslint/consistent-type-imports` | error | keeps `import type` honest for the package boundary rules |
| `import/no-extraneous-dependencies` or `no-restricted-imports` per §6 | error | |

ESLint gives a rule one level, so there is one error line and no warning band. The aim figures (250 lines, 60 per function) are written
in `AGENTS.md` for the agent to steer by. A warning band would need a second lint run for little gain; add it only if files keep landing just under 400.

React (`apps/*`, `packages/app-react`): `react-hooks/rules-of-hooks` error, `react-hooks/exhaustive-deps` error, plus
`@tanstack/query/flat/recommended` once React Query is in (nothing imports it today; the plugin is added with the first query).
`max-lines-per-function` for `*.tsx` is 250 because components are JSX.

Tests (`**/test/**`, `*.test.ts(x)`): file limit 600; function limit off (a `describe` callback is one long function by design);
`no-floating-promises` stays on.

Exemption list (`ignores`):

- `contract/protocol.ts`, `contract/fixtures/**`, `contract/prompts/**` (generated or captured data; the generator is checked by its own test)
- `**/dist/**`, `**/.turbo/**`, `**/node_modules/**`, `pnpm-lock.yaml`
- `*.css` is not linted by ESLint; style files get Prettier only. If a CSS limit is wanted later, Biome's CSS file rule can be the one use of Biome.
- A file-level `/* eslint-disable max-lines -- <why> */` is allowed. `reportUnusedDisableDirectives: "error"` makes a stale one fail, and the
  reason text is required by a `eslint-comments/require-description` rule or by review. Zero exist today (no `eslint-disable` anywhere), so a new one is
  visible in a diff.

## 6. Shared config in `packages/config`

`packages/config` already holds the two tsconfigs. Add:

```
packages/config/
  eslint/base.js     exports base = [js.configs.recommended, ...tseslint.configs.recommended, sizeRules, importRules]
  eslint/react.js    exports react = [...base, reactHooks, query, jsxSizeRules]
  eslint/test.js     exports test: relaxed limits for the test globs
  prettier.json      { "printWidth": 140, "singleQuote": true, "trailingComma": "all" }
```

Prettier settings are what the code already looks like: 139 lines in the TS files exceed 140 characters, single quotes, no semicolon change; run it once
and check the diff is small before fixing the width. Each package has:

```js
// packages/app-core/eslint.config.js
import { base, test } from '@handline/config/eslint';
export default [...base, ...test, { languageOptions: { parserOptions: { projectService: true } } }];
```

Import boundaries are written once in `base` as `no-restricted-imports` patterns by folder, which is enough for six packages:

| Folder | May not import |
|---|---|
| `packages/client`, `packages/tokens` | `react`, `react-dom`, `react-native`, any `@handline/app-*` |
| `packages/app-core` | `react*`, `react-native`, DOM globals (`no-restricted-globals`: `window`, `document`) |
| `packages/*` | anything under `apps/` or `web/` |
| everywhere | `@handline/*/src/*` (deep imports; use the package entry) |
| `apps/*` | `packages/fake-daemon` outside `test/` and `vite.config.ts` |

These repeat what `docs/turborepo-layout.md` says in words; the rule makes it fail. If the table passes about ten rows, move to
`eslint-plugin-boundaries` ([project](https://github.com/javierbrea/eslint-plugin-boundaries)); before that the extra plugin buys nothing
([plain `no-restricted-imports` is enough for module boundaries](https://timdeschryver.dev/bits/enforce-module-boundaries-with-no-restricted-imports)).
`dependency-cruiser` is the choice only if we want a cycle check on the whole graph; the pnpm workspace already rejects undeclared dependencies at install.

## 7. Where it runs

1. `turbo.json` gets `"lint": { "inputs": ["src/**", "test/**", "eslint.config.js", "$TURBO_ROOT$/packages/config/**"] }`. It does **not** depend on `^lint`:
   lint reads sources only, so packages run in parallel and cache independently.
2. Every package gets `"lint": "eslint ."` and the root gets `"format:check": "prettier --check ."`.
3. The merge gate in `docs/turborepo-layout.md` is `pnpm turbo test typecheck`; it becomes `pnpm turbo test typecheck lint` plus the format check.
   The Python side of the gate (`tools/land.sh`, `tools/suite.sh`) already runs outside turbo and does not change.
4. Agents run `pnpm turbo lint` before asking for review; one line in `AGENTS.md` says that a `max-lines` error means *split the file by
   responsibility*, not raise the limit or add a disable.
5. No pre-commit hook for lint: agents commit often on branches, and the gate is the place that counts.

## 8. Adopting on the code we have

The TypeScript is 45 files, about 4.4k lines. Fix everything now:

1. One commit that only runs Prettier. Record it in `.git-blame-ignore-revs`.
2. One commit that adds the config and the `lint` task, with the rule set at the levels above, and the fixes it needs. Count of known problems today:
   one file over 400 lines, no function lengths measured yet (the first run reports them; expect a few components near 60).
3. If a rule cannot be satisfied in that commit, use ESLint's bulk suppressions: `eslint --fix --suppress-all` writes `eslint-suppressions.json`,
   it is committed, and `--prune-suppressions` removes entries as files are fixed; stale ones are reported
   ([announcement](https://eslint.org/blog/2025/04/introducing-bulk-suppressions/)). Use it per rule (`--suppress-rule`), never for everything.
4. The same limits for **Python and Go** are a separate decision: Python source is up to 2600 lines per file and a test file is 9400, Go source tops out at
   442. A Go and Python gate would be a small script in `tools/` over the same metric. Not covered here.

## 9. Splitting today's files

Raw line counts (`wc -l`, so blank and comment lines included; the lint counts less):

| Size | TS files |
|---|---|
| > 400 | 1: `packages/fake-daemon/src/daemon.ts` 486 |
| 300-400 | `packages/client/src/client.ts` 336, `packages/fake-daemon/test/client.test.ts` 301 (test, under its limit) |
| 200-300 | `app-core/src/workspace.ts` 274, `app-core/test/store.test.ts` 249, `app-core/test/workspace.test.ts` 217 |
| Whole tree | median 58, 90th percentile 217, apps and `web/` have no file over 200 |

Per area: `packages` 23 files, 2651 lines; `apps/web` 11 files, 774; `web/` (retiring) 11 files, 949.

Splits, in order of need:

- **`fake-daemon/src/daemon.ts` (486, must split).** One class, `FakeDaemon`, holds the connection loop, session state, every method handler, the input
  holder, dialogs and the terminal rows. Split by what changes together: `daemon.ts` (class, connection loop, dispatch), `sessions.ts` (the `Session`
  type and row building), `handlers/terminal.ts`, `handlers/input.ts`, `handlers/dialog.ts`, `errors.ts` (`Failure`, `refused`, `badRequest`). The
  planned bytes work adds a handler; it lands as a new file instead of 100 more lines here.
- **`client/src/client.ts` (336).** `TerminalView` (lines 43-86) to `terminal-view.ts`; `RpcError` and the exported types to `errors.ts` and `types.ts`;
  `DaemonClient` stays. Below 250 after that.
- **`app-core/src/workspace.ts` (274).** Tree helpers (`groupsOf`, `mapNode`, `detach`, `replaceGroup`) to `layout-tree.ts`; tab operations
  (`openTab`, `closeTab`, `activate`, `tabId`) stay. Test file follows the same cut.
- Nothing else is over the warning line. `Sidebar.tsx` (147) and the CSS are fine.

Each split is a pure move: same exports through the package entry, tests unchanged, one commit per file so a reviewer sees "moved, not edited".
The terminal pane work (`docs/terminal-pane.md`) touches `TerminalTab.tsx` and `packages/client`; do the `client.ts` split first so that
unit does not grow it.

## 10. Decisions for the founder

1. **The numbers.** Error at 400 lines per source file, 600 per test file, 120 per function. Raise or lower once the first run shows function lengths.
2. **ESLint + Prettier** (recommended) or Biome for one tool and fewer plugins. The query and hooks rules decide it.
3. **Python and Go** get a size limit too, later, by a script in `tools/`.

## 11. Not verified

- No lint tool is installed, so the rules and thresholds were not run on the code; the first run will show function lengths.
- Peer ranges with ESLint 10 (§4) could not be checked: the sandbox cannot reach the npm registry.
- The rule options above are taken from the linked documentation pages, not from running them.
- Evidence on agent accuracy by file size is indirect (§3).
