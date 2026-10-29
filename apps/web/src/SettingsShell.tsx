// The frame all four tabs of the Settings area share: a title and the tab strip that stay put while the content scrolls under them, and one content width.
import { useState, type ReactNode } from 'react';
import { Icon, PageHead, PageTitle, Reveal, ScrollPage, SectionHead } from '#src/ui/index.ts';
import { ContentsPage, type Part } from '#src/ContentsPage.tsx';
import { SettingsTabs, type Tab } from '#src/SettingsTabs.tsx';

/** `parts` puts a contents list at the left and the content 640 wide beside it, flush left; without it the tab keeps the page column. */
export function SettingsShell({ tab, label, parts, children }: { tab: Tab; label: string; parts?: readonly Part[]; children: ReactNode }) {
  return (
    <ScrollPage className="settings-shell" aria-label={label} beside={!!parts}>
      <PageHead>
        <PageTitle>Settings</PageTitle>
        <SettingsTabs value={tab} />
      </PageHead>
      {parts ? <ContentsPage parts={parts}>{children}</ContentsPage> : <div className="flex min-w-0 flex-col gap-4">{children}</div>}
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

/** A section that stays one line until the person opens it; its rows then appear in place. */
export function SettingsFold({ id, title, hint, children }: { id?: string; title: string; hint: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <section id={id}>
      <button
        type="button"
        aria-expanded={open}
        className="flex w-full cursor-pointer items-center gap-2 rounded-md border border-line bg-surface px-4 py-2 text-left"
        onClick={() => setOpen(!open)}
      >
        <Icon name={open ? 'less' : 'more'} size={14} label={open ? `Fold ${title}` : `Show ${title}`} />
        <span className="font-medium">{title}</span>
        <span className="text-xs text-ink-3">{hint}</span>
      </button>
      <Reveal show={open}>
        <div className="flex flex-col gap-4 pt-4">{children}</div>
      </Reveal>
    </section>
  );
}
