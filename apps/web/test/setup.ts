// The tests run with reduced motion on: lists swap at once. test/motion.test.tsx turns it off to see the animation.
const real = window.matchMedia?.bind(window);
window.matchMedia = (q: string) => {
  const m = real ? real(q) : ({ addEventListener() {}, removeEventListener() {}, media: q } as unknown as MediaQueryList);
  return q.includes('prefers-reduced-motion') ? Object.defineProperty(m, 'matches', { value: true }) : m;
};
