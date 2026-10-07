import { test } from 'node:test';
import assert from 'node:assert/strict';
import { gatewayAddress, magicDnsName, onTailnet, tailscaleAddress } from '../src/tailnet.ts';

const nic = (address: string) => [{ address, family: 'IPv4', internal: false }] as never;

test('only a 100.64.0.0/10 address on a utun or tailscale interface is the Tailscale address', () => {
  assert.equal(tailscaleAddress({ utun4: nic('100.101.2.3') }), '100.101.2.3');
  assert.equal(tailscaleAddress({ tailscale0: nic('100.64.0.1') }), '100.64.0.1');
  assert.equal(tailscaleAddress({ en0: nic('100.101.2.3') }), null); // the LAN may hand that range out
  assert.equal(tailscaleAddress({ utun4: nic('100.200.1.1') }), null);
  assert.equal(tailscaleAddress({ utun4: nic('10.0.0.5'), lo0: nic('127.0.0.1') }), null);
});

test('the gateway is on the Tailscale address when it is up, loopback when not, and HANDLINE_GATEWAY wins', () => {
  assert.equal(gatewayAddress(undefined, { utun4: nic('100.101.2.3') }), '100.101.2.3:7443');
  assert.equal(gatewayAddress(undefined, { en0: nic('192.168.0.4') }), '127.0.0.1:7443');
  assert.equal(gatewayAddress('127.0.0.1:17444', { utun4: nic('100.101.2.3') }), '127.0.0.1:17444');
});

test('the MagicDNS name is Self.DNSName without its trailing dot, and only a ts.net name counts', () => {
  assert.equal(magicDnsName('{"Self":{"DNSName":"mac.tail1234.ts.net."}}'), 'mac.tail1234.ts.net');
  assert.equal(magicDnsName('{"Self":{"DNSName":"mac.example.com."}}'), null);
  assert.equal(magicDnsName('{"Self":{}}'), null);
  assert.equal(magicDnsName('not json'), null);
  assert.equal(onTailnet('100.101.2.3:7443'), true);
  assert.equal(onTailnet('127.0.0.1:7443'), false);
});
