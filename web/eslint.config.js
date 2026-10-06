import tseslint from 'typescript-eslint';
import { base, test } from '@handline/config/eslint';

// web/mobile is plain browser script outside any tsconfig; web/ retires with apps/web.
export default [...base, ...test, { files: ['mobile/**'], ...tseslint.configs.disableTypeChecked }];
