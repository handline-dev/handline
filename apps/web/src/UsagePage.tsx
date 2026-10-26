// The Usage page (#/stats), in the rail under Tasks and what the status bar's usage item opens: each account's gauges, then the Stats section.
import { PageHead, PageTitle, ScrollPage, SectionHead, SettingsCard } from '#src/ui/index.ts';
import { UsageRow, useAccountList } from '#src/Accounts.tsx';
import { StatsSection } from '#src/Stats.tsx';
import { View } from '#src/View.tsx';

export function UsagePage() {
  const { q, served, now, list } = useAccountList();
  return (
    <ScrollPage className="usage-page" aria-label="Usage">
      <PageHead>
        <PageTitle>Usage</PageTitle>
      </PageHead>
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
