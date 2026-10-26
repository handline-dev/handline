package main

// usages is each verb's form and one example, printed when its arguments are refused: "<verb> <sub>" first, then "<verb>".
var usages = map[string][2]string{
	"say":      {`hl say <target> "<text>" [--parent <msg>] [--task T<n> | --no-task "<why>"] [--file PATH] [--attach FILE]`, `hl say human "T12 is done: PR #400" --task T12`},
	"read":     {`hl read <msg> [--wait] [--timeout <span>]`, `hl read msg_1a2b3c4`},
	"inbox":    {`hl inbox [--all] [--wait] [--timeout <span>]`, `hl inbox --all`},
	"withdraw": {`hl withdraw <msg> [--reason "<why>"]`, `hl withdraw msg_1a2b3c4 --reason "sent to the wrong agent"`},
	"progress": {`hl progress "<done: ... left: ...>"`, `hl progress "done: parser; left: tests"`},
	"team":     {`hl team [--agent <name>]`, `hl team`},
	"chat":     {`hl chat search "<text>" [--with <name>] [--since <span>] [--limit N]`, `hl chat search "nav order" --with handline --since 2d`},
	"task":     {`hl task <new|show|list|board|done|handoff|edit|comment|begin|start|reopen|release|discard|accept|reject|queue|draft|merge|request|none> ...`, `hl task show T12`},
	"task new": {`hl task new --assign <agent> -t <span> "<title>" "<body>" [--owner <agent>] [--parent T<n>] [--blocked-by T<n>] [--priority 1-5] [--estimate N] [--expects report|decision] [--tag T] [--file PATH]`,
		`hl task new --assign pc -t 2h "Fix the sidebar" "The badge overlaps the name"`},
	"task show":    {`hl task show T<n>`, `hl task show T12`},
	"task list":    {`hl task list [--assign <agent>] [--owner <agent>] [--state <state>] [--tag T] [--all] [--everyone]`, `hl task list --state doing`},
	"task board":   {`hl task board [--everyone]`, `hl task board`},
	"task done":    {`hl task done T<n> "<evidence>" [--request <msg>] [--repo PATH] [--commit SHA] [--shot FILE]`, `hl task done T12 "PR #400 (sha 1a2b3c4), tests green"`},
	"task handoff": {`hl task handoff T<n> <agent> "<note>" [--file PATH]`, `hl task handoff T12 auditor "Review fix@1a2b3c4"`},
	"task edit": {`hl task edit T<n> [--assign <agent>] [--owner <agent>] [--title T] [--body B | --body-file PATH] [--priority 1-5] [--estimate N] [--blocked-by T<m>] [--unblock T<m>] [--timeout <span>] [--tag T] [--untag T] [--pr N]`,
		`hl task edit T12 --blocked-by T13`},
	"task comment":    {`hl task comment T<n> add "<text>" [--attach FILE] [--now]   |   list   |   edit <id> "<text>"   |   delete <id>`, `hl task comment T12 add "The fix also needs the phone"`},
	"task memo":       {`hl task memo T<n> ... (the old name of hl task comment)`, `hl task comment T12 add "The fix also needs the phone"`},
	"task begin":      {`hl task begin "<title>" --chat <msg> [--why W] [--priority 1-5] [--expects report|decision]`, `hl task begin "Answer the founder" --chat msg_1a2b3c4`},
	"task start":      {`hl task start T<n>`, `hl task start T12`},
	"task reopen":     {`hl task reopen T<n> "<why>"`, `hl task reopen T12 "the badge still overlaps on the phone"`},
	"task release":    {`hl task release T<n> "<why>"`, `hl task release T12 "blocked on the founder"`},
	"task wait":       {`hl task wait T<n> (--on <vendor>:<target> | --pr <n> [--repo owner/name]) [--until ci|closed|done] [-t <span>]`, `hl task wait T12 --pr 412`},
	"task discard":    {`hl task discard T<n> "<why>"   |   hl task discard T<n> T<m> --why "<why>" [--why-each T<m>="<why>"]`, `hl task discard T12 "duplicate of T9"`},
	"task request":    {`hl task request <msg> ["<title>"] [--owner <agent>]`, `hl task request msg_1a2b3c4 "Rework the sidebar"`},
	"task none":       {`hl task none <msg> "<why>"`, `hl task none msg_1a2b3c4 "a thanks, nothing to do"`},
	"task note":       {`hl task note T<n> "<text>"`, `hl task note T12 "the phone needs it too"`},
	"task queue":      {`hl task queue T<n> [T<m> ...]`, `hl task queue T12`},
	"task draft":      {`hl task draft T<n> [T<m> ...]`, `hl task draft T12`},
	"task merge":      {`hl task merge T<n> T<m> [...]`, `hl task merge T12 T13`},
	"hook":            {`hl hook <event>`, `hl hook Stop`},
	"hr":              {`hl hr <add|retire|rename|link|unlink|team|context|bundle|list|withdraw> ...`, `hl hr list`},
	"hr add":          {`hl hr add <agent> --repo PATH --why "<why>" [--team T] [--model M] [--effort E] [--vendor V]`, `hl hr add shop.sea --repo /code/sea --why "split the storefront work"`},
	"hr retire":       {`hl hr retire <agent> --why "<why>"`, `hl hr retire old.agent --why "its work moved to new.agent"`},
	"hr rename":       {`hl hr rename <agent> --to <new name> --why "<why>" [--team T]`, `hl hr rename shop.sea --to shop.ocean --why "clearer name"`},
	"hr link":         {`hl hr link <from> <to> --why "<why>"`, `hl hr link reviewer backend --why "backend may ask the reviewer"`},
	"hr unlink":       {`hl hr unlink <from> <to> --why "<why>"`, `hl hr unlink reviewer backend --why "no longer needed"`},
	"hr team":         {`hl hr team <manager> --member <name>[=REPO] ... --repo PATH --why "<why>"`, `hl hr team shop --member sea=/code/sea --member shore --repo /code/shop --why "a storefront team"`},
	"hr context":      {`hl hr context <agent> [--role-file PATH] [--base-file PATH] --why "<why>"`, `hl hr context shop.sea --role-file role.md --why "its role grew"`},
	"hr list":         {`hl hr list [--json] [--all]`, `hl hr list`},
	"hr withdraw":     {`hl hr withdraw <proposal id>`, `hl hr withdraw 1a2b3c4d`},
	"init":            {`hl init`, `hl init`},
	"service":         {`hl service install|uninstall [--allow-live-home]`, `hl service install`},
	"launch":          {launchUsage, `hl launch shop.sea`},
	"sandbox-repo":    {sandboxRepoUsage, `hl sandbox-repo /code/sea npm`},
	"device":          {`hl device pair [--name N]`, `hl device pair --name phone`},
	"dev":             {`hl dev on|off|rebuild <team> [--worktree PATH] [--listen ADDR] [--fresh]`, `hl dev on shop`},
	"idle-background": {`hl idle-background [--notice <span>] [--grace <span>] [--check <span>] [--clear]`, `hl idle-background --grace 10m`},
	"notice-digest":   {`hl notice-digest [<span>] [--agent <name>] [--clear]`, `hl notice-digest 2m --agent shop.sea`},
}

// usageOf is the form and example for args to verb: its subverb's when it has one, else its own.
func usageOf(verb string, args []string) ([2]string, bool) {
	if len(args) > 0 {
		if u, ok := usages[verb+" "+args[0]]; ok {
			return u, true
		}
	}
	u, ok := usages[verb]
	return u, ok
}
