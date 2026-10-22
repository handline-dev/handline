// The tabs of the Settings area: the settings themselves, then Team (who is who) and HR (the proposals that wait for the founder).
import { useHrCount, useServes } from '@handline/app-data';
import { Icon, TabStrip } from '#src/ui/index.ts';
import { goTeam } from '#src/route.ts';

type Tab = 'settings' | 'team' | 'hr';

export function SettingsTabs({ value }: { value: Tab }) {
  const { served: hrServed } = useServes('hr.list');
  const count = useHrCount();
  return (
    <TabStrip
      label="Settings pages"
      value={value}
      onChange={(t: Tab) => (t === 'settings' ? void (location.hash = '#/settings') : goTeam(t))}
      tabs={[
        { value: 'settings', label: 'Settings' },
        { value: 'team', label: 'Team' },
        ...(hrServed
          ? [
              {
                value: 'hr' as const,
                label: (
                  <>
                    {count ? (
                      <span className="hr-dot text-bad" aria-hidden="true">
                        <Icon name="decision" size={12} className="mr-0.5 inline-block align-[-2px]" />{' '}
                      </span>
                    ) : null}
                    HR{count ? ` ${count}` : ''}
                  </>
                ),
              },
            ]
          : []),
      ]}
    />
  );
}
