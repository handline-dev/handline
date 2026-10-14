// The photo library and the image editor are native; the screens that import them draw in a DOM, where a test replaces `src/ImagePick.ts` itself.
export const launchImageLibraryAsync = async () => ({ canceled: true, assets: [] });
export const manipulateAsync = async (uri: string) => ({ uri });
export const SaveFormat = { PNG: 'png', JPEG: 'jpeg' } as const;
