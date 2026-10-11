// The states a data view draws besides its content (docs/turborepo-layout.md §8.3): skeleton, not served, error with Retry, empty.
import type { ReactNode } from 'react';
import { classify, sentence, Skeleton, viewState, type SkeletonName } from '@handline/app-data';
import { Button } from './ui/index.ts';

export function View(props: {
  q: { isPending: boolean; isFetching: boolean; isError: boolean; data: unknown; error: unknown; refetch: () => unknown };
  served: boolean;
  /** The first-load shape and what it is called for the screen reader. */
  shape: SkeletonName;
  what: string;
  empty?: string | false;
  children: ReactNode;
}) {
  const { q, served, shape, what, empty = false } = props;
  const state = viewState(q, served, !!empty);
  if (state === 'not_served') return <p className="muted pad">This daemon does not serve {what}.</p>;
  if (state === 'loading') return <Skeleton shape={shape} loading label={what} />;
  if (state === 'empty') return <p className="muted pad">{empty}</p>;
  const failure = q.isError ? classify(q.error) : null;
  const text = failure ? sentence(failure) : '';
  return (
    <>
      {text && failure?.kind !== 'not_found' ? (
        <div className="banner" role="alert">
          {text} <Button variant="outline" size="sm" label="Retry" onClick={() => void q.refetch()} />
        </div>
      ) : null}
      {props.children}
    </>
  );
}
