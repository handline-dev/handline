// Self-update of the Android build from GitHub releases. Two channels: nightly (prereleases) and stable (full releases). Every release
// body carries a "versionCode: N" line (the CI run number, so it only grows); a release is an update when its number is above the
// installed one, which makes switching channels need no special case: Stable to Nightly offers the newer nightly, Nightly to Stable
// waits for a stable release that is newer than the installed build.
export type Channel = 'nightly' | 'stable';

export interface Release {
  tag_name: string;
  name?: string | null;
  body?: string | null;
  draft: boolean;
  prerelease: boolean;
  assets: { name: string; browser_download_url: string }[];
}

export interface Update {
  versionName: string;
  versionCode: number;
  url: string;
}

const API = 'https://api.github.com/repos/handline-dev/handline/releases';
// Stable asks for the newest full release itself: a page of mixed releases would push it out after about 30 nightlies.
export const releasesUrl = (channel: Channel) => (channel === 'stable' ? `${API}/latest` : `${API}?per_page=30`);

const codeOf = (r: Release): number | null => {
  const m = /^versionCode:\s*(\d+)\s*$/m.exec(r.body ?? '');
  return m ? Number(m[1]) : null;
};

/** The newest release of the channel that is newer than the installed build, or null. */
export function pickUpdate(releases: Release[], channel: Channel, installed: number): Update | null {
  let best: Update | null = null;
  for (const r of releases) {
    if (r.draft || r.prerelease !== (channel === 'nightly')) continue;
    const code = codeOf(r);
    const apk = r.assets.find((a) => a.name.endsWith('.apk'));
    if (code === null || !apk || code <= installed) continue;
    if (!best || code > best.versionCode) {
      best = { versionName: apk.name.replace(/^handline-/, '').replace(/\.apk$/, ''), versionCode: code, url: apk.browser_download_url };
    }
  }
  return best;
}

export async function checkForUpdate(channel: Channel, installed: number, fetchFn: typeof fetch = fetch): Promise<Update | null> {
  const res = await fetchFn(releasesUrl(channel), { headers: { Accept: 'application/vnd.github+json' } });
  if (!res.ok) throw new Error(`could not read releases (${res.status})`);
  const body = (await res.json()) as Release | Release[];
  return pickUpdate(Array.isArray(body) ? body : [body], channel, installed);
}
