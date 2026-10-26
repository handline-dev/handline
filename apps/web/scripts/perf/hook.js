// Loaded into the page before the app: a minimal React DevTools hook. Each commit adds, per component that rendered in it (visited in this
// commit and did work, flag PerformedWork), one render and its own time.
(() => {
  const P = (window.__perf = { on: false, comps: {}, commits: [] });
  const KINDS = new Set([0, 1, 11, 14, 15]); // function, class, forwardRef, memo, simple memo
  const nameOf = (f) => {
    let t = f.type;
    if (t && t.type) t = t.type;
    if (t && t.render) t = t.render;
    return (t && (t.displayName || t.name)) || 'anonymous';
  };
  window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = {
    renderers: new Map(),
    supportsFiber: true,
    isDisabled: false,
    inject(r) {
      this.renderers.set(this.renderers.size + 1, r);
      return this.renderers.size;
    },
    checkDCE() {},
    onScheduleFiberRoot() {},
    onCommitFiberUnmount() {},
    onPostCommitFiberRoot() {},
    setStrictMode() {},
    onCommitFiberRoot(_id, root) {
      if (!P.on) return;
      const top = root.current;
      const commit = { ms: top.actualDuration, renders: 0 };
      const stack = [top];
      while (stack.length) {
        const f = stack.pop();
        if (f.actualStartTime >= top.actualStartTime && KINDS.has(f.tag) && f.flags & 1) {
          const c = (P.comps[nameOf(f)] ??= { renders: 0, ms: 0 });
          c.renders++;
          c.ms += f.selfBaseDuration;
          commit.renders++;
        }
        if (f.sibling) stack.push(f.sibling);
        if (f.child) stack.push(f.child);
      }
      P.commits.push(commit);
    },
  };
})();
