// react-native-reanimated is native; the tests only need a shared value to hold a number.
export const useSharedValue = <T>(v: T) => ({ value: v });
