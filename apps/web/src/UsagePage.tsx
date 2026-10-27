// The Usage page (#/stats): the outlook, each account's gauges, then Stats.
import { useSelector } from '@handline/app-react';
import { OutlookSection } from '#src/Outlook.tsx';
import { PageHead, PageTitle, ScrollPage, SectionHead, SettingsCard } from '#src/ui/index.ts';
import { UsageRow, useAccountList } from '#src/Accounts.tsx';
import { StatsSection } from '#src/Stats.tsx';
import { View } from '#src/View.tsx';

export function UsagePage() {
  const { q, served, now, list } = useAccountList();
  const outlook = useSelector((s) => s.can('stats.outlook'));
  return (
    <ScrollPage className="usage-page" aria-label="Usage">
      <PageHead>
        <PageTitle>Usage</PageTitle>
      </PageHead>
      {outlook ? <OutlookSection /> : null}
      <View q={q} served={served} shape="accounts" what="accounts" empty={!list.length && 'No accounts configured.'}>
        <SectionHead>Accounts</SectionHead>
        <SettingsCard className="acct-list flex flex-col">
          {list.map((account) => (
            <UsageRow key={account.name} account={account} now={now} />
          ))}
        </SettingsCard>
      </View>
      <StatsSection />
    </ScrollPage>
  );
}
