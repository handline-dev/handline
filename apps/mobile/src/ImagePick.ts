// The photo library on the phone: the person picks images, and each comes back ready to upload (an allowed type, shrunk to the long edge,
// under the size cap), as the shared draft hook takes them.
import { launchImageLibraryAsync } from 'expo-image-picker';
import { SaveFormat, manipulateAsync } from 'expo-image-manipulator';
import { fitLongEdge } from '@handline/app-core';
import { IMAGE_TYPES, MAX_IMAGE, type ImageSource } from '@handline/app-data';

export async function pickImages(room: number): Promise<ImageSource[]> {
  if (room < 1) return [];
  const r = await launchImageLibraryAsync({ mediaTypes: ['images'], allowsMultipleSelection: true, selectionLimit: room, quality: 1 });
  if (r.canceled) return [];
  return Promise.all(
    r.assets.slice(0, room).map(async (a) => {
      let uri = a.uri;
      let mime = a.mimeType ?? 'image/jpeg';
      let name = a.fileName ?? uri.split('/').pop() ?? 'image';
      const fit = fitLongEdge(a.width, a.height);
      const allowed = (IMAGE_TYPES as string[]).includes(mime);
      // A photo is re-encoded as JPEG when its type is not one the daemon takes (HEIC), or it is too large even after shrinking.
      if (fit || !allowed || (a.fileSize ?? 0) > MAX_IMAGE) {
        const png = allowed && mime === 'image/png';
        const out = await manipulateAsync(a.uri, fit ? [{ resize: fit }] : [], {
          compress: 0.9,
          format: png ? SaveFormat.PNG : SaveFormat.JPEG,
        });
        uri = out.uri;
        mime = png ? 'image/png' : 'image/jpeg';
        name = name.replace(/\.[^.]+$/, '') + (png ? '.png' : '.jpg');
      }
      const blob = await (await fetch(uri)).blob();
      return { blob, name, mime, preview: uri };
    }),
  );
}
