// The end of a paged task list: older rows load when it scrolls into view; a window without IntersectionObserver keeps a button.
import { TASK_PAGE } from '@handline/app-data';
import { useLoadOnScroll } from '#src/useLoadOnScroll.ts';
import { Button } from '#src/ui/index.ts';

export function LoadOlder(props: { shown: number; loading: boolean; load: () => void; className?: string }) {
  const { shown, loading, load, className = '' } = props;
  const { end, supported } = useLoadOnScroll(true, loading, load);
  return (
    <div ref={end} className={`flex items-center justify-center gap-3 text-xs text-ink-2 ${className}`}>
      {supported ? (
        <span>{loading ? 'Loading…' : ''}</span>
      ) : (
        <>
          <span>{shown} loaded · older ones exist</span>
          <Button
            variant="outline"
            size="sm"
            label={`Show ${TASK_PAGE} older`}
            aria-label={`Show ${TASK_PAGE} older`}
            disabled={loading}
            onClick={load}
          >
            {loading ? 'Loading…' : `Show ${TASK_PAGE} older`}
          </Button>
        </>
      )}
    </div>
  );
}
