// What an approved proposal that did not apply says: one line, the daemon's own words behind Details.
import type { Proposal } from '@handline/client';
import { applyFailure } from '@handline/app-core';
import { FNote } from '#src/ui/index.ts';

export function ApplyNote({ p }: { p: Proposal }) {
  const { line, details } = applyFailure(p);
  return (
    <FNote tone="bad">
      {line}
      {details ? (
        <details className="muted">
          <summary>Details</summary>
          {details}
        </details>
      ) : null}
    </FNote>
  );
}
