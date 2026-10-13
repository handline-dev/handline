import { keptParams } from './attachments.ts';
import { badRequest } from './errors.ts';

/** A write's successful reply, kept by request_id: the same id with the same params returns it, repeating nothing. */
export class Replies {
  private kept = new Map<string, { params: string; result: Record<string, unknown> }>();

  /** `fresh` lets a reply that holds a moving state (a delivery receipt) be brought up to date when it is replayed. */
  once(
    p: Record<string, unknown>,
    make: () => Record<string, unknown>,
    fresh: (r: Record<string, unknown>) => Record<string, unknown> = (r) => r,
  ): Record<string, unknown> {
    const { request_id, ...rest } = p as { request_id: string };
    const sig = JSON.stringify(keptParams(rest));
    const kept = this.kept.get(request_id);
    if (kept) {
      if (kept.params !== sig) throw badRequest('That request_id was used for different params.', 'request_id');
      return { ...fresh(kept.result), replayed: true };
    }
    const result = make();
    this.kept.set(request_id, { params: sig, result });
    return result;
  }
}
