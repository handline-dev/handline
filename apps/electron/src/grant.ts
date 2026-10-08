// What the shell tells its page about the daemon it owns: the device token, the one host the page may reach over plain ws, and why the
// daemon is not up when it is not, so the page shows that instead of a refusal to connect to an address nothing listens on.

export interface Grant {
  token: string | null;
  ownHost: string | null;
  failure: string | null;
}

export function grant(token: string | null, ownHost: string | null, owner: { state: string; reason: string }): Grant {
  const failure = owner.state === 'failed' ? owner.reason || 'the daemon did not start' : null;
  return { token, ownHost, failure };
}
