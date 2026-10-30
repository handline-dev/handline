// The link a browser opens to be invited: the gateway's own address and the one-time code, from the `handline://pair` line `device.pair` gives.
// The code is in the fragment, so it never reaches a server log or a Referer.

/** `https` when the gateway has a certificate, else `http` (Tailscale carries the encryption). Null when the line is not a pairing line or the
 * gateway listens on this computer only, which no other computer can reach. */
export function inviteLink(uri: string, encrypted: boolean): string | null {
  const q = uri.startsWith('handline://pair?') ? new URLSearchParams(uri.slice('handline://pair?'.length)) : null;
  const host = q?.get('host');
  const code = q?.get('code');
  if (!host || !code || /^(127\.|localhost|\[?::1\]?)/.test(host)) return null;
  return `${encrypted ? 'https' : 'http'}://${host}/#pair=${encodeURIComponent(code)}`;
}
