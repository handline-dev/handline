// The size limits, in one place. Counts skip blank lines and comment-only lines.
export const limits = {
  fileLines: 400,
  testFileLines: 600,
  functionLines: 120,
  componentLines: 250, // *.tsx: components are long JSX
  depth: 4,
};
