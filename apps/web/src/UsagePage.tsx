// The Usage page (#/stats), in the rail under Tasks and what the status bar's usage item opens: the week left (when the daemon serves the outlook),
// the accounts one line each, then who used it; the accounts' settings are in Settings.
import { useOutlook } from '@handline/app-data';
import { useSelector } from '@handline/app-react';
import { OutlookSection, useRange } from '#src/Outlook.tsx';
import { PageHead, PageTitle, ScrollPage } from '#src/ui/index.ts';
import { StatsSection } from '#src/Stats.tsx';
import { UsageAccounts } from '#src/UsageAccounts.tsx';

export function UsagePage() {
  const outlook = useSelector((s) => s.can('stats.outlook'));
  const [range, setRange] = useRange();
  const q = useOutlook(range);
  return (
    <ScrollPage className="usage-page" aria-label="Usage">
      <PageHead>
        <PageTitle>Usage</PageTitle>
      </PageHead>
      {outlook ? <OutlookSection q={q} range={range} setRange={setRange} /> : null}
      <UsageAccounts runway={q.data?.runway} />
      <StatsSection />
    </ScrollPage>
  );
}
