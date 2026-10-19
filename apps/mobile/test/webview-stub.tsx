// react-native-webview is native: the test draws an element that carries the address it was given.
export const WebView = ({ source, testID }: { source: { uri: string }; testID?: string }) => <div data-testid={testID} data-uri={source.uri} />;
