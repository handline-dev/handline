// expo-clipboard is native: the test keeps what was copied here.
export const copied: string[] = [];
export const setStringAsync = async (text: string): Promise<boolean> => (copied.push(text), true);
