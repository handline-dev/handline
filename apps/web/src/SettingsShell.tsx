// The frame all four tabs of the Settings area share: a title and the tab strip that stay put while the content scrolls under them, and one content width.
import type { ReactNode } from 'react';
import { PageHead, PageTitle, ScrollPage, SectionHead } from '#src/ui/index.ts';
import { SettingsTabs, type Tab } from '#src/SettingsTabs.tsx';

export function SettingsShell({ tab, label, children }: { tab: Tab; label: string; children: ReactNode }) {
  return (
    <ScrollPage className="settings-shell" aria-label={label}>
      <PageHead>
        <PageTitle>Settings</PageTitle>
        <div className="basis-full">
          <SettingsTabs value={tab} />
        </div>
      </PageHead>
      <div className="flex min-w-0 flex-col gap-4">{children}</div>
    </ScrollPage>
  );
}

/** A section: its caps heading, a tab's action at the heading's right, then the content. */
export function SettingsSection({
  title,
  action,
  children,
  ...rest
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
  id?: string;
}) {
  return (
    <section {...rest}>
      <div className="flex items-center justify-between gap-2">
        <SectionHead>{title}</SectionHead>
        {action}
      </div>
      {children}
    </section>
  );
}
