// What the seeds share.
import type { FakeDaemon } from '#src/daemon.ts';

/** Puts a task row in under a fixed number. */
export const task = (d: FakeDaemon, id: number, more: Parameters<FakeDaemon['tasks']['seed']>[0]) => d.tasks.seed({ ...more, id });
