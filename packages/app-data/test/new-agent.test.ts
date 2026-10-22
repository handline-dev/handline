import { describe, expect, it } from 'vitest';
import { setup } from '@handline/fake-daemon/helpers';
import { EFFORTS, EMPTY_AGENT_FIELDS, createParams, shapeChoices } from '#src/index.ts';

describe('the new-agent choices', () => {
  it("are the daemon's options, with a logged-out account shown and not offered", async () => {
    const { client } = setup();
    await client.start();
    const o = await client.call('agent.options', {});
    const c = shapeChoices(o);
    expect(c).toMatchObject({ fromDaemon: true, efforts: o.efforts, models: o.models, freeNames: o.free_names });
    expect(c.accounts.find((a) => a.name === 'gamma')).toMatchObject({ login_state: 'needs_login', offered: false });
    expect(c.accounts.find((a) => a.name === 'alpha')?.offered).toBe(true);
    client.close();
  });

  it("fall back to today's lists without agent.options", () => {
    expect(shapeChoices(undefined)).toEqual({ fromDaemon: false, efforts: EFFORTS, models: null, accounts: [], roles: [], freeNames: [] });
  });

  it('put the account in agent.create only when one is picked', () => {
    const repo = { path: '/work/handline', default_branch: 'main' };
    const f = { ...EMPTY_AGENT_FIELDS, name: 'x', role: 'worker', repo: repo.path };
    expect(createParams(f, repo)).not.toHaveProperty('account');
    expect(createParams({ ...f, account: 'beta' }, repo)).toMatchObject({ account: 'beta' });
  });
});
