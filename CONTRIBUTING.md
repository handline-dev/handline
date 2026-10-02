# Contributing

Thanks for wanting to help. Two things to know before you open a pull request.

## 1. Sign the contributor license agreement

orcapipe is released under the GNU Affero General Public License v3.0, and the project owner also
offers commercial licenses to people who cannot meet the AGPL's terms. To be able to do both, the
project needs the right to distribute every contribution under other licenses as well. The
[CLA](CLA.md) gives it, in plain words. You keep the copyright in what you wrote.

**How to sign.** Read [CLA.md](CLA.md), then add this exact sentence as a comment on your first
pull request:

> I have read the CLA Document and I hereby sign the CLA

One signature covers every later contribution from the same GitHub account. If you contribute on
behalf of an employer, make sure you are allowed to, and that the employer is fine with the CLA.
A maintainer checks for the comment before merging, and may ask a CLA bot to record it. A pull
request without it is not merged.

## 2. What a good change looks like

- Tests for what you change, in the same commit. `tools/test.sh` runs them; see
  [docs/development.md](docs/development.md).
- Commit messages, code comments and docs say what changed and why, in words. They carry no
  personal data (emails, real names, home paths, tokens) and no ids from a private tracker or chat.
  `tools/localrefs.py` shows what would be refused.
- English everywhere in the repository.
- A new source file starts with `# SPDX-License-Identifier: AGPL-3.0-only`. Files that predate this
  rule do not need one added; the [LICENSE](LICENSE) covers the whole repository.

## Reporting a problem

Open an issue with what you ran, what you expected and what happened.
