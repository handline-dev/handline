import { base, test } from '@handline/config/eslint';
export default [...base(import.meta.dirname), ...test];
