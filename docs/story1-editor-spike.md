# Story 1 spike: WYSIWYG Markdown editor (T3985)

Rule: stored as Markdown, edited and shown rich, source view optional, images saved as `![alt](attachment:<id>)`.

Probe: scratch package outside the repo, jsdom, each editor loads the sample (headings, nested and ordered lists, bold, italic, link, code span, attachment image) and serialises it back. Bundles are esbuild --minify with only the pieces needed (gzip).

| Candidate | Version / licence / last release | Downloads / week | Bundle (min, gz) | Round trip on the sample | Phone path | Verdict |
|---|---|---|---|---|---|---|
| Tiptap 3 (ProseMirror) + `@tiptap/markdown` + StarterKit + Image | 3.32.0, MIT, 2026-10-13 | 3.7M (core 22M) | 462 KB, 145 KB | Identical (only the final newline differs). Image with `attachment:` src kept as is. | WebView hosting the same bundle | Pick |
| Milkdown 7 (ProseMirror, remark) + commonmark | 7.22.3, MIT, 2026-10-10 | 0.64M | 365 KB, 111 KB | Bullets `-` become `*` (remark default, configurable); everything else identical. | WebView | Second |
| Lexical 0.52 + `@lexical/markdown` | 0.52.0, MIT, 2026-10-14 | 6M | about 414 KB, 136 KB (before the image node) | Nested lists indent 4 spaces, not 2. Image stays plain text: needs our own ImageNode and transformer. | Lexical has a React Native build, but no markdown/rich-text there; WebView in practice | Not picked |

## Edge cases (Tiptap, same probe)

Same: code fence with language, blockquote, ordered list starting at 3, external image, `[x](javascript:...)` (href is emptied).

Differs on re-save:
- `_italic_` / `__bold__` become `*italic*` / `**bold**` (harmless, but the stored text changes on first save of an old body).
- Raw HTML is parsed, not shown as text: `text <script>alert(1)</script> <b>x</b>` becomes `text  **x**`; the script text is lost and `<b>` turns bold. Breaks rule 4 (raw HTML shown as text); needs the HTML parse rules off (escape `<` on load) before use.
- `/tmp/a_b_c.ts` is saved as `/tmp/a\_b\_c.ts`: paths with underscores get escaped. Agent text is full of paths, so this needs a fix (or a serializer override) before it edits existing bodies.
- A hard break `\` + newline is saved as two spaces + newline.

Not run: Milkdown and Lexical on the edge cases above.

## Phone

Not verified. `apps/mobile` has no `react-native-webview` today, so a WebView needs a dev client rebuild (same as the annotation pick, T3924). All three candidates are DOM editors, so the phone path is the same for all: a WebView page serving the web bundle, Markdown passed in and out over `postMessage`. Open: images inside it use the shared image component only if the host supplies the `attachment:` URLs through the same bridge. A native phone editor was not looked at.

## Pick

Tiptap with `@tiptap/markdown`: the exact round trip on the sample, the largest maintained ecosystem, a React node view for our own image component, 3.x released yesterday. Cost: about 145 KB gz, and two serializer fixes (raw HTML, underscore escaping) before it may touch stored bodies.
