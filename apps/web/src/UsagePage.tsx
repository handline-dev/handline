// The Usage page (#/stats), in the rail under Tasks and what the status bar's usage item opens: the week left (or the cost per day) when the daemon
// serves the outlook, the accounts one line each, then who used it; the accounts' settings are in Settings.
import { useOutlook } from '@handline/app-data';
import { useSelector } from '@handline/app-react';
import { useAccountList } from '#src/Accounts.tsx';
import { OutlookSection, useLook } from '#src/Outlook.tsx';
import { PageHead, PageTitle, ScrollPage } from '#src/ui/index.ts';
import { StatsSection } from '#src/Stats.tsx';
import { UsageAccounts } from '#src/UsageAccounts.tsx';

export function UsagePage() {
  const outlook = useSelector((s) => s.can('stats.outlook'));
  const [look, setLook] = useLook();
  const q = useOutlook(look.range);
  const accounts = useAccountList();
  const labels = Object.fromEntries(accounts.list.map((a) => [a.name, a.label]));
  return (
    <ScrollPage className="usage-page" aria-label="Usage">
      <PageHead>
        <PageTitle>Usage</PageTitle>
      </PageHead>
      {outlook ? <OutlookSection q={q} look={look} setLook={setLook} labels={labels} /> : null}
      <UsageAccounts accounts={accounts} outlook={q.data} />
      <StatsSection />
    </ScrollPage>
  );
}
