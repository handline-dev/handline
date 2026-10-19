// Expo config plugin: the prebuilt React Native core hides RCTPackagerConnection, which expo-dev-launcher links against, so the
// iOS dev client fails to link. Building React Native from source keeps native modules like react-native-svg working.
const { withPodfileProperties } = require('expo/config-plugins');

module.exports = (config) =>
  withPodfileProperties(config, (c) => {
    c.modResults['ios.buildReactNativeFromSource'] = 'true';
    return c;
  });
