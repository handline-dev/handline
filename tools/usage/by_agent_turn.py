"""Usage by agent and by turn: python3 tools/usage/by_agent_turn.py calls.pkl
Points are 1% of a 5h window, priced by the weights in W below, which are
fitted (fit.py), not published: refit before quoting them."""
import pickle,collections,numpy as np,bisect
calls,users=pickle.load(open(__import__("sys").argv[1],"rb"))
W = dict(inp=17.5, cc=21.9, cr=0.134, out=98.4)  # % of a 5h window per M tokens, fitted 2026-09-21 on 56 intervals
def pts(c): return (c["inp"]*W["inp"]+c["cc"]*W["cc"]+c["cr"]*W["cr"]+c["out"]*W["out"])/1e6
def agent(c):
    p=c["cwd"] or ""; parts=p.strip("/").split("/")
    return "/".join(parts[-2:]) if len(parts)>1 else p
tot=sum(pts(c) for c in calls)
print("fleet total (5h-window points, 5 days, 5 accounts): %.0f"%tot)
ag=collections.defaultdict(lambda:[0,0.0,0,0,0])
for c in calls:
    a=ag[agent(c)]; a[0]+=1;a[1]+=pts(c);a[2]+=c["cr"]+c["cc"]+c["inp"];a[3]+=c["out"];a[4]+=c["cc"]
print("agent: calls, pts, share, mean ctx, mean out, models")
mods=collections.defaultdict(collections.Counter)
for c in calls: mods[agent(c)][c["model"].replace("claude-","")]+=1
for k,v in sorted(ag.items(),key=lambda x:-x[1][1])[:16]:
    print(f"{k:38s} {v[0]:6d} {v[1]:7.0f} {100*v[1]/tot:5.1f}%  ctx {v[2]/v[0]:8.0f} out {v[3]/v[0]:6.0f}  {dict(mods[k].most_common(2))}")
# turns
ps=collections.defaultdict(list)
for s,t,kind,size,origin in users:
    if kind=="prompt": ps[s].append(t)
for s in ps: ps[s].sort()
turns=collections.defaultdict(list)
orph=0
for c in calls:
    l=ps.get(c["sess"])
    if not l: orph+=1; continue
    i=bisect.bisect_right(l,c["ts"])-1
    if i<0: orph+=1; continue
    turns[(c["sess"],l[i])].append(c)
print("turns",len(turns),"calls outside any prompt",orph)
tc=sorted(((sum(pts(c) for c in cs),len(cs),cs) for cs in turns.values()),key=lambda x:-x[0])
allp=np.array([x[0] for x in tc]);print("turn pts: median %.3f mean %.3f p90 %.3f p99 %.3f max %.2f"%(np.median(allp),allp.mean(),np.percentile(allp,90),np.percentile(allp,99),allp.max()))
top=int(len(tc)*0.05); topsum=sum(x[0] for x in tc[:top]); print("top 5%% of turns (%d) carry %.1f%% of turn cost"%(top,100*topsum/sum(allp)))
ncalls=np.array([x[1] for x in tc]);
print("calls/turn: median %d mean %.1f | top5%% mean %.1f"%(np.median(ncalls),ncalls.mean(),np.mean([x[1] for x in tc[:top]])))
print("correlation of turn cost with n calls %.2f"%np.corrcoef(allp,ncalls)[0,1])
# what dominates top turns: share by class
def cls(cs):
    return [sum(c[k] for c in cs) for k in ("inp","cc","cr","out")]
for name,sel in (("top5%",[x[2] for x in tc[:top]]),("rest",[x[2] for x in tc[top:]])):
    flat=[c for cs in sel for c in cs]; v=cls(flat)
    p=[v[0]*W["inp"]/1e6,v[1]*W["cc"]/1e6,v[2]*W["cr"]/1e6,v[3]*W["out"]/1e6]; s=sum(p)
    print(name,"cost split fresh/cc/cr/out: "+" ".join("%.0f%%"%(100*x/s) for x in p),"mean ctx %.0f"%(sum(v[:3])/len(flat)))
tools=collections.Counter(); toolp=collections.Counter()
for _,_,cs in tc[:top]:
    for c in cs:
        for t in set(c["tools"]): tools[t]+=1
allt=collections.Counter()
for _,_,cs in tc[top:]:
    for c in cs:
        for t in set(c["tools"]): allt[t]+=1
print("tools per call in top turns:",[(t,round(n/sum(x[1] for x in tc[:top]),2)) for t,n in tools.most_common(6)])
print("tools per call in rest     :",[(t,round(n/sum(x[1] for x in tc[top:]),2)) for t,n in allt.most_common(6)])
