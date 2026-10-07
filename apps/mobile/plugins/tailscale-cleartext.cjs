// Expo config plugin: the phone reaches a plain Tailscale gateway as ws://, which the OS refuses by default. Cleartext is allowed
// for *.ts.net only, never globally (Tailscale's tunnel encrypts it). Android's network security config names domains, not CIDR
// ranges; the app trusts a no-fingerprint link only for a *.ts.net name, never a bare IP.
const fs = require('node:fs');
const path = require('node:path');
const { withAndroidManifest, withDangerousMod, withInfoPlist } = require('expo/config-plugins');

const DOMAIN = 'ts.net';
// The Debug variant loads its JS from Metro on localhost, the emulator's 10.0.2.2 or a LAN address, over cleartext; a network security
// config makes Android ignore the debug manifest's usesCleartextTraffic, so Debug gets its own that allows it.
const debugXml = () => `<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
  <base-config cleartextTrafficPermitted="true" />
</network-security-config>
`;
const androidXml = () => `<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
  <domain-config cleartextTrafficPermitted="true">
    <domain includeSubdomains="true">${DOMAIN}</domain>
  </domain-config>
</network-security-config>
`;
/** The ATS exception for *.ts.net, merged into whatever Info.plist already holds. */
const atsOf = (ats = {}) => ({
  ...ats,
  NSExceptionDomains: {
    ...ats.NSExceptionDomains,
    [DOMAIN]: { NSIncludesSubdomains: true, NSExceptionAllowsInsecureHTTPLoads: true },
  },
});

module.exports = (config) => {
  config = withAndroidManifest(config, (c) => {
    c.modResults.manifest.application[0].$['android:networkSecurityConfig'] = '@xml/network_security_config';
    return c;
  });
  config = withDangerousMod(config, [
    'android',
    (c) => {
      const dir = path.join(c.modRequest.platformProjectRoot, 'app/src/main/res/xml');
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(path.join(dir, 'network_security_config.xml'), androidXml());
      const debug = path.join(c.modRequest.platformProjectRoot, 'app/src/debug/res/xml');
      fs.mkdirSync(debug, { recursive: true });
      fs.writeFileSync(path.join(debug, 'network_security_config.xml'), debugXml());
      return c;
    },
  ]);
  return withInfoPlist(config, (c) => {
    c.modResults.NSAppTransportSecurity = atsOf(c.modResults.NSAppTransportSecurity);
    return c;
  });
};
module.exports.androidXml = androidXml;
module.exports.debugXml = debugXml;
module.exports.atsOf = atsOf;
