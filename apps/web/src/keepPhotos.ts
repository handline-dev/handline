import { createContext } from 'react';

/** Under it, a picture cannot be deleted from the viewer: a task's brief keeps its photos. */
export const KeepPhotos = createContext(false);
