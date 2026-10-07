import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const plugin = createRequire(import.meta.url)('../plugins/tailscale-cleartext.cjs') as {
  androidXml(): string;
  debugXml(): string;
  atsOf(ats?: object): { NSAllowsLocalNetworking?: boolean; NSExceptionDomains: Record<string, object> };
};

test('Android permits cleartext for *.ts.net only; every other host stays on the platform default', () => {
  const xml = plugin.androidXml();
  assert.match(xml, /<domain includeSubdomains="true">ts\.net<\/domain>/);
  assert.equal(xml.match(/cleartextTrafficPermitted="true"/g)?.length, 1);
  assert.ok(!xml.includes('base-config')); // no base: cleartext is refused
});

test('iOS adds a ts.net exception and keeps what Info.plist already had', () => {
  const ats = plugin.atsOf({ NSAllowsLocalNetworking: true, NSExceptionDomains: { 'example.org': {} } });
  assert.equal(ats.NSAllowsLocalNetworking, true);
  assert.deepEqual(ats.NSExceptionDomains['example.org'], {});
  assert.deepEqual(ats.NSExceptionDomains['ts.net'], { NSIncludesSubdomains: true, NSExceptionAllowsInsecureHTTPLoads: true });
});

test('the Debug variant has its own config that lets Metro load over cleartext, and the release one has no such base', () => {
  assert.match(plugin.debugXml(), /<base-config cleartextTrafficPermitted="true"/);
  assert.ok(!plugin.androidXml().includes('base-config'));
});
