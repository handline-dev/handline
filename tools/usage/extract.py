"""Read every API call out of the Claude transcripts of all accounts into one
pickle for the other scripts here: python3 tools/usage/extract.py calls.pkl
One record per request id (a call streams as several lines; the largest output
is kept), plus the user rows that start a turn. Reads only the conversation
logs under ~/.orcapipe/accounts/*/.claude/projects."""
import json,glob,os,pickle,sys
from datetime import datetime
calls={}  # (session,req) -> rec
users=[]  # (session,ts,kind,size)
def ts(s):
    return datetime.fromisoformat(s.replace("Z","+00:00")).timestamp()
files=glob.glob(os.path.expanduser("~/.orcapipe/accounts/*/.claude/projects/*/**/*.jsonl"),recursive=True)
for f in files:
    acct=f.split("/accounts/")[1].split("/")[0]
    sub="/subagents/" in f
    for ln in open(f,errors="replace"):
        if '"usage"' not in ln and '"type":"user"' not in ln: continue
        try: o=json.loads(ln)
        except Exception: continue
        t=o.get("type")
        if t=="assistant":
            m=o.get("message",{}); u=m.get("usage")
            if not u or not o.get("requestId") or m.get("model","").startswith("<"): continue
            k=(o.get("sessionId"),o["requestId"])
            tools=[c.get("name") for c in m.get("content",[]) if c.get("type")=="tool_use"]
            r=calls.get(k)
            out=u.get("output_tokens",0)
            if r is None or out>=r["out"]:
                calls[k]=dict(acct=acct,cwd=o.get("cwd"),sess=o.get("sessionId"),ts=ts(o["timestamp"]),model=m.get("model"),
                  inp=u.get("input_tokens",0),cc=u.get("cache_creation_input_tokens",0),cr=u.get("cache_read_input_tokens",0),
                  out=out,side=bool(o.get("isSidechain")) or sub,tools=tools if tools else (r or {}).get("tools",[]),file=os.path.basename(f))
            elif tools and r is not None: r["tools"]=list(set(r["tools"])|set(tools))
            if r is not None and tools: calls[k]["tools"]=sorted(set(calls[k]["tools"])|set(tools))
        elif t=="user":
            m=o.get("message",{}); c=m.get("content")
            isres=isinstance(c,list) and any(isinstance(x,dict) and x.get("type")=="tool_result" for x in c)
            size=len(json.dumps(c)) if c is not None else 0
            users.append((o.get("sessionId"),ts(o["timestamp"]),"result" if isres else "prompt",size,(o.get("origin") or {}).get("kind") if isinstance(o.get("origin"),dict) else None))
pickle.dump((list(calls.values()),users),open(sys.argv[1],"wb"))
print(len(files),"files",len(calls),"calls",len(users),"user rows")
