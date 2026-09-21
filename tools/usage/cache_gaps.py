"""Cache-write tokens by the idle gap before the call: python3 tools/usage/cache_gaps.py calls.pkl
Answers how much is paid to rebuild a context after the cache lapsed (1h) or a
session started, against the steady growth mid-session."""
import pickle,collections,numpy as np
calls,users=pickle.load(open(__import__("sys").argv[1],"rb"))
calls.sort(key=lambda c:(c["sess"],c["ts"]))
def ctx(c): return c["inp"]+c["cc"]+c["cr"]
# gap to previous call in the same session (main thread only)
prev={}
rows=[]
for c in calls:
    k=(c["sess"],c["side"],c["file"])
    p=prev.get(k); prev[k]=c
    gap=None if p is None else c["ts"]-p["ts"]
    rows.append((c,gap))
def bucket(g):
    if g is None: return "first call of session file"
    if g<300: return "<5m"
    if g<3600: return "5m-1h"
    return ">1h"
agg=collections.defaultdict(lambda:[0,0,0,0,0])
for c,g in rows:
    a=agg[bucket(g)]; a[0]+=1;a[1]+=c["cc"];a[2]+=c["cr"];a[3]+=c["out"];a[4]+=ctx(c)
totcc=sum(a[1] for a in agg.values())
print("gap bucket: calls, share of cc tokens, mean cc/call, mean ctx/call, cc as share of ctx")
for b,a in agg.items():
    print(f"{b:28s} {a[0]:6d} calls  {100*a[1]/totcc:5.1f}% of cc  mean cc {a[1]/a[0]:8.0f}  mean ctx {a[4]/a[0]:8.0f}  cc/ctx {a[1]/max(a[4],1):.2f}")
# full-rebuild calls: cc >= 50% of context
rb=[c for c,g in rows if ctx(c)>0 and c["cc"]/ctx(c)>=0.5]
print("rebuild calls (cc>=50% of ctx):",len(rb),"of",len(rows),"; their share of cc tokens %.1f%%"%(100*sum(c["cc"] for c in rb)/totcc))
gb=collections.Counter(bucket(g) for c,g in rows if ctx(c)>0 and c["cc"]/ctx(c)>=0.5)
print("rebuild calls by gap bucket:",dict(gb))
# steady growth: <5m calls cc mean
