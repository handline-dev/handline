// Expo config plugin: a release build made by CI. The version comes from the environment (versionCode is the CI run number, so it
// only grows) and the APK is signed with the one fixed release key; a local build without these keeps the debug key and app.json.
const { withAppBuildGradle } = require('expo/config-plugins');

const patch = (gradle, env) => {
  let out = gradle;
  if (env.HANDLINE_VERSION_CODE) {
    out = out.replace(/versionCode \d+/, `versionCode ${Number(env.HANDLINE_VERSION_CODE)}`);
    out = out.replace(/versionName "[^"]*"/, `versionName "${env.HANDLINE_VERSION_NAME}"`);
  }
  if (env.HANDLINE_KEYSTORE_FILE) {
    out = out.replace(
      /signingConfigs \{/,
      `signingConfigs {
        release {
            storeFile file(System.getenv("HANDLINE_KEYSTORE_FILE"))
            storePassword System.getenv("HANDLINE_KEYSTORE_PASSWORD")
            keyAlias System.getenv("HANDLINE_KEY_ALIAS")
            keyPassword System.getenv("HANDLINE_KEYSTORE_PASSWORD")
        }`,
    );
    out = out.replace(/(buildTypes \{[\s\S]*?release \{[\s\S]*?)signingConfig signingConfigs\.debug/, '$1signingConfig signingConfigs.release');
  }
  return out;
};

module.exports = (config) =>
  withAppBuildGradle(config, (c) => {
    c.modResults.contents = patch(c.modResults.contents, process.env);
    return c;
  });
module.exports.patch = patch;
