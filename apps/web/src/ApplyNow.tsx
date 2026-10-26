// "Apply now" on a proposal held for a running turn: the daemon interrupts the turns it waits for and applies the change at once. The card
// changes when the hr topic says it is approved; a refusal that it already settled needs no words, the refetch shows it.
import { useState } from 'react';
import type { Proposal } from '@handline/client';
import { classify, sentence, useWrite } from '@handline/app-data';
import { useSelector } from '@handline/app-react';
import { Button } from '#src/ui/index.ts';

export function ApplyNow({ p, refresh }: { p: Proposal; refresh?: () => void }) {
  const can = useSelector((s) => s.can('hr.apply_now'));
  const apply = useWrite('hr.apply_now', String(p.id));
  const [problem, setProblem] = useState('');
  if (!can) return null;
  return (
    <>
      <Button
        variant="outline"
        icon="play"
        label="Apply now"
        size="sm"
        disabled={apply.isPending}
        onClick={() => {
          setProblem('');
          apply.mutateAsync({ proposal: p.id }).then(refresh, (e) => {
            if ((e as { gate?: string }).gate !== 'proposal_settled') setProblem(sentence(classify(e)));
            else refresh?.();
          });
        }}
      >
        {apply.isPending ? '…' : 'Apply now'}
      </Button>
      {problem ? (
        <span className="text-bad text-label" role="status">
          {problem}
        </span>
      ) : null}
    </>
  );
}
