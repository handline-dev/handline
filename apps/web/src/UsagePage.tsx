// The Usage page (#/stats), in the rail under Tasks and what the status bar's usage item opens: the usage outlook (when the daemon serves it), then the Stats section; the accounts are in Settings.
import { useSelector } from '@handline/app-react';
import { OutlookSection } from '#src/Outlook.tsx';
import { PageHead, PageTitle, ScrollPage } from '#src/ui/index.ts';
import { StatsSection } from '#src/Stats.tsx';

export function UsagePage() {
  const outlook = useSelector((s) => s.can('stats.outlook'));
  return (
    <ScrollPage className="usage-page" aria-label="Usage">
      <PageHead>
        <PageTitle>Usage</PageTitle>
      </PageHead>
      {outlook ? <OutlookSection /> : null}
      <StatsSection />
    </ScrollPage>
  );
}
