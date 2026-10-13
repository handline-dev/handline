package main

import (
	"fmt"
	"path/filepath"
	"slices"
	"strings"

	"golang.org/x/sys/unix"

	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/paths"
	"github.com/handline/handline/go/internal/textsafe"
)

// taskCmd is one `hl task` subcommand (cli/task.py): it reads its parsed arguments, and ok false hands the argv to Python.
type taskCmd func(c *hlclient.Client, args []string) (int, bool)

var taskCmds = map[string]taskCmd{
	"new": taskNew, "begin": taskBegin, "handoff": taskHandoff, "start": taskStart, "note": taskNote,
	"memo": taskMemo, "show": taskShow, "list": taskList, "queue": taskQueue(true), "draft": taskQueue(false),
	"edit": taskEdit, "merge": taskMerge, "done": taskDone, "reopen": taskReopen, "release": taskRelease,
	"discard": taskDiscard, "board": taskBoard, "accept": taskAccept, "reject": taskReject,
}

// taskReads are the subcommands that change nothing, so no verb_chain is recorded for them (parser._TASK_READS).
var taskReads = []string{"list", "show", "board"}

func task(c *hlclient.Client, args []string) (int, bool) {
	if len(args) == 0 {
		return 0, false
	}
	run := taskCmds[args[0]]
	if run == nil {
		return 0, false
	}
	return run(c, args[1:])
}

// mutating records the verb chain the way parser.main does once the arguments parsed, for every write.
func mutating(c *hlclient.Client, sub string) {
	if !slices.Contains(taskReads, sub) {
		c.NoteVerbChain([]string{"task", sub})
	}
}

// asking is the asking agent, or false after printing why it cannot be named.
func asking(c *hlclient.Client, verb string) bool {
	if c.AskingAgent() == "" {
		fmt.Println(hlclient.WhoRefusal(verb))
		return false
	}
	return true
}

// said prints what a write said, and its exit code (cli/client.said).
func said(ok bool, text string) int {
	fmt.Println(text)
	if ok {
		return 0
	}
	return 1
}

// optTask, optPriority and optTimeout are an optional option of that type: (value, given, ok).
func optTask(p parsed, name string) (int, bool, bool) {
	v, given := p.opt(name)
	if !given {
		return 0, false, true
	}
	n, ok := taskRef(v)
	return n, true, ok
}

func optPriority(p parsed, name string) (int, bool, bool) {
	v, given := p.opt(name)
	if !given {
		return 0, false, true
	}
	n, ok := priority(v)
	return n, true, ok
}

func optTimeout(p parsed, name string) (int64, bool, bool) {
	v, given := p.opt(name)
	if !given {
		return 0, false, true
	}
	n, ok := taskTimeout(v)
	return n, true, ok
}

// oneTask is a subcommand that takes T<n> and nothing else.
func oneTask(args []string) (int, bool) {
	p, ok := parse(args, nil, nil, nil)
	if !ok || len(p.pos) != 1 {
		return 0, false
	}
	return taskRef(p.pos[0])
}

func taskNew(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, []string{"--file", "--assign", "--owner", "--parent", "--blocked-by", "--priority", "--expects", "--timeout"},
		nil, map[string]string{"-t": "--timeout"})
	if !ok || len(p.pos) < 1 || len(p.pos) > 2 {
		return 0, false
	}
	// --assign is who works the task; --owner is who answers for it, the opener when not given.
	assignee, hasAssign := p.opt("--assign")
	accountable, hasOwner := p.opt("--owner")
	parent, hasParent, ok1 := optTask(p, "--parent")
	blocked, hasBlocked, ok2 := optTask(p, "--blocked-by")
	prio, hasPrio, ok3 := optPriority(p, "--priority")
	timeout, hasTimeout, ok4 := optTimeout(p, "--timeout")
	if !ok1 || !ok2 || !ok3 || !ok4 { // --expects is accepted and ignored: a done is a report its owner accepts
		return 0, false
	}
	mutating(c, "new")
	if !asking(c, "open a task") {
		return 1, true
	}
	file, _ := p.opt("--file")
	var body *string
	if len(p.pos) == 2 {
		body = &p.pos[1]
	}
	typed := func(title string) string {
		parts := []string{"hl task new", "--assign", assignee}
		if hasTimeout {
			t := fmt.Sprintf("%ds", timeout)
			if timeout%60 == 0 {
				t = fmt.Sprintf("%dm", timeout/60)
			}
			parts = append(parts, "-t", t)
		}
		parts = append(parts, shQuote(title))
		if hasOwner {
			parts = append(parts, "--owner", accountable)
		}
		if body != nil && *body != "-" {
			parts = append(parts, shQuote(*body))
		}
		if file != "" {
			parts = append(parts, "--file", shQuote(file))
		}
		if hasParent {
			parts = append(parts, fmt.Sprintf("--parent T%d", parent))
		}
		if hasBlocked {
			parts = append(parts, fmt.Sprintf("--blocked-by T%d", blocked))
		}
		if hasPrio {
			parts = append(parts, fmt.Sprintf("--priority P%d", prio))
		}
		return strings.Join(parts, " ")
	}
	if !hasAssign {
		if hasOwner { // --owner named who works a task until owner and assignee were split
			assignee, hasOwner = accountable, false
		} else {
			assignee = "<agent>"
		}
		fmt.Printf("a task needs --assign: who works it (--owner is who answers for it, you when not given). Try:\n  %s\n", typed(p.pos[0]))
		return 1, true
	}
	for _, who := range []string{assignee, accountable} {
		if who != "" && who != hlclient.Human && !slices.Contains(paths.ListAgents(c.Home), who) {
			fmt.Printf("no agent named %s\n", who)
			return 1, true
		}
	}
	title := textsafe.Strip(textsafe.Oneline(p.pos[0]))
	if title == "" {
		fmt.Printf("a task needs a title. Try:\n  %s\n", typed("<title>"))
		return 1, true
	}
	inline := ""
	if body != nil {
		inline = *body
	}
	if file != "" || inline == "-" {
		text, why := textArg(body, file, "body")
		if why != "" {
			fmt.Println(why)
			return 1, true
		}
		inline = text
	}
	params := map[string]any{"owner": assignee, "title": title}
	if hasTimeout {
		params["time_box"] = fmt.Sprintf("%ds", timeout)
	}
	if b := textsafe.Strip(textsafe.Text(inline)); b != "" {
		params["body"] = b
	}
	if hasParent {
		params["parent"] = parent
	}
	if hasBlocked {
		params["blocked_by"] = blocked
	}
	if hasPrio {
		params["priority"] = prio
	}
	if hasOwner {
		params["reporter"] = accountable
	}
	done, msg, got := c.Written("task.new", params)
	fmt.Println(msg)
	if !done {
		return 1, true
	}
	fmt.Printf("T%v\n", got["task"]) // the number this opened, alone on the last line, for a script to read
	return 0, true
}

func taskBegin(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, []string{"--chat", "--why", "--priority", "--expects"}, nil, nil)
	if !ok || len(p.pos) != 1 {
		return 0, false
	}
	chatText, hasChat := p.opt("--chat")
	why, hasWhy := p.opt("--why")
	chat, ok1 := integer(chatText)
	prio, hasPrio, ok2 := optPriority(p, "--priority")
	exp := "report"
	if v, given := p.opt("--expects"); given {
		if exp, ok = expects(v); !ok {
			return 0, false
		}
	}
	if !hasChat || !ok1 || !ok2 {
		return 0, false
	}
	mutating(c, "begin")
	if !asking(c, "begin a task") {
		return 1, true
	}
	title := textsafe.Strip(textsafe.Oneline(p.pos[0]))
	if title == "" {
		fmt.Println("a task needs a title")
		return 1, true
	}
	params := map[string]any{"title": title, "message": chat, "expects": exp}
	if hasWhy {
		params["why"] = why
	}
	if hasPrio {
		params["priority"] = prio
	}
	return said(c.Write("task.begin", params)), true
}

func taskHandoff(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, []string{"--file"}, nil, nil)
	if !ok || len(p.pos) < 2 || len(p.pos) > 3 {
		return 0, false
	}
	tid, ok := taskRef(p.pos[0])
	if !ok {
		return 0, false
	}
	mutating(c, "handoff")
	if !asking(c, "hand a task over") {
		return 1, true
	}
	var inline *string
	if len(p.pos) == 3 {
		inline = &p.pos[2]
	}
	file, _ := p.opt("--file")
	note, why := textArg(inline, file, "note")
	if why != "" {
		fmt.Println(why)
		return 1, true
	}
	return said(c.Write("task.handoff", map[string]any{"task": tid, "to": p.pos[1], "note": note})), true
}

func taskStart(c *hlclient.Client, args []string) (int, bool) {
	tid, ok := oneTask(args)
	if !ok {
		return 0, false
	}
	mutating(c, "start")
	if !asking(c, "start a task") {
		return 1, true
	}
	return said(c.Write("task.start", map[string]any{"task": tid})), true
}

func taskNote(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, nil, nil, nil)
	if !ok || len(p.pos) != 2 {
		return 0, false
	}
	tid, ok := taskRef(p.pos[0])
	if !ok {
		return 0, false
	}
	mutating(c, "note")
	if !asking(c, "write a memo") {
		return 1, true
	}
	return said(c.Write("task.memo.add", map[string]any{"task": tid, "text": p.pos[1]})), true
}

func taskMemo(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, []string{"--file"}, nil, nil)
	if !ok || len(p.pos) < 2 {
		return 0, false
	}
	tid, ok := taskRef(p.pos[0])
	verb, rest := p.pos[1], slices.Clone(p.pos[2:])
	shape, known := map[string]int{"list": 0, "add": 1, "edit": 2, "delete": 1}[verb]
	if !ok || !known {
		return 0, false
	}
	var mid int
	if verb == "edit" || verb == "delete" { // an id Python's int() reads some other way, or refuses, is Python's to say
		if len(rest) > 0 {
			if mid, ok = integer(strings.TrimSpace(rest[0])); !ok {
				return 0, false
			}
		}
	}
	mutating(c, "memo")
	if !asking(c, "write a memo") {
		return 1, true
	}
	file, _ := p.opt("--file")
	dash := len(rest) > 0 && rest[len(rest)-1] == "-"
	if (verb == "add" || verb == "edit") && (file != "" || dash) {
		var inline *string
		if dash {
			inline = &rest[len(rest)-1]
			rest = rest[:len(rest)-1]
		}
		text, why := textArg(inline, file, "memo text")
		if why != "" {
			fmt.Println(why)
			return 1, true
		}
		rest = append(rest, text)
	}
	if len(rest) != shape {
		fmt.Println(map[string]string{"list": "list takes nothing more", "add": "add takes the memo text",
			"edit": "edit takes the memo id, then the new text", "delete": "delete takes the memo id"}[verb])
		return 1, true
	}
	switch verb {
	case "list":
		return memoList(c, tid), true
	case "add":
		return said(c.Write("task.memo.add", map[string]any{"task": tid, "text": rest[0]})), true
	}
	if verb == "edit" {
		return said(c.Write("task.memo.edit", map[string]any{"task": tid, "memo": mid, "text": rest[1]})), true
	}
	return said(c.Write("task.memo.delete", map[string]any{"task": tid, "memo": mid})), true
}

func taskQueue(queued bool) taskCmd {
	sub, method, verb := "draft", "task.draft", "draft a task"
	if queued {
		sub, method, verb = "queue", "task.queue", "queue a task"
	}
	return func(c *hlclient.Client, args []string) (int, bool) {
		p, ok := parse(args, nil, nil, nil)
		if !ok || len(p.pos) == 0 {
			return 0, false
		}
		tids, ok := taskRefs(p.pos)
		if !ok {
			return 0, false
		}
		mutating(c, sub)
		if !asking(c, verb) {
			return 1, true
		}
		failed := false
		for _, tid := range tids { // one call each: a refusal of one does not hold back the rest
			done, msg := c.Write(method, map[string]any{"tasks": []int{tid}})
			fmt.Println(msg)
			failed = failed || !done
		}
		if failed {
			return 1, true
		}
		return 0, true
	}
}

func taskEdit(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, []string{"--assign", "--owner", "--title", "--body", "--body-file", "--priority", "--blocked-by", "--unblock",
		"--timeout"}, nil, nil)
	if !ok || len(p.pos) != 1 {
		return 0, false
	}
	tid, ok := taskRef(p.pos[0])
	prio, hasPrio, ok1 := optPriority(p, "--priority")
	blocked, hasBlocked, ok2 := optTask(p, "--blocked-by")
	unblock, hasUnblock, ok3 := optTask(p, "--unblock")
	timeout, hasTimeout, ok4 := optTimeout(p, "--timeout")
	if !ok || !ok1 || !ok2 || !ok3 || !ok4 {
		return 0, false
	}
	mutating(c, "edit")
	if !asking(c, "edit a task") {
		return 1, true
	}
	// --owner moved the work until owner and assignee were split; alone it would now quietly leave the work where it is.
	if owner, given := p.opt("--owner"); given {
		if _, assigned := p.opt("--assign"); !assigned {
			fmt.Printf("--owner is who answers for a task, not who works it, so it needs --assign too. To move the work:\n  hl task edit T%d --assign %s\n"+
				"To change only who answers for it, name the assignee it keeps:\n  hl task edit T%d --owner %s --assign <its assignee>\n", tid, owner, tid, owner)
			return 1, true
		}
	}
	params := map[string]any{"task": tid}
	body, hasBody := p.opt("--body")
	bodyFile, _ := p.opt("--body-file")
	if hasBody || bodyFile != "" {
		var inline *string
		if hasBody {
			inline = &body
		}
		text, why := textArg(inline, bodyFile, "body")
		if why != "" {
			fmt.Println(why)
			return 1, true
		}
		params["body"] = text
	}
	if v, given := p.opt("--assign"); given { // the wire's owner is who works the task, its reporter who answers for it
		params["owner"] = v
	}
	if v, given := p.opt("--owner"); given {
		params["reporter"] = v
	}
	if v, given := p.opt("--title"); given {
		params["title"] = v
	}
	if hasPrio {
		params["priority"] = prio
	}
	if hasTimeout {
		params["time_box"] = fmt.Sprintf("%ds", timeout)
	}
	if hasBlocked {
		params["blocked_by"] = blocked
	}
	if hasUnblock {
		params["unblock"] = unblock
	}
	if len(params) == 1 {
		fmt.Println("edit needs a new assignee, a new owner, a new title, --body, --priority, --blocked-by, --unblock, or --timeout")
		return 1, true
	}
	return said(c.Write("task.edit", params)), true
}

func taskMerge(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, nil, nil, nil)
	if !ok || len(p.pos) < 2 {
		return 0, false
	}
	tids, ok := taskRefs(p.pos)
	if !ok {
		return 0, false
	}
	mutating(c, "merge")
	if !asking(c, "merge tasks") {
		return 1, true
	}
	return said(c.Write("task.merge", map[string]any{"into": tids[0], "tasks": tids[1:]})), true
}

func taskDone(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, []string{"--request", "--repo", "--commit", "--shot"}, []string{"--reason"}, nil)
	if !ok || len(p.pos) < 1 || len(p.pos) > 2 {
		return 0, false
	}
	tid, ok := taskRef(p.pos[0])
	if !ok {
		return 0, false
	}
	req, hasReq := p.opt("--request")
	request, ok := integer(req)
	if hasReq && !ok {
		return 0, false
	}
	mutating(c, "done")
	if !asking(c, "mark a task done") {
		return 1, true
	}
	// A sha is checked against the repo this is typed in unless --repo names another.
	repo, _ := p.opt("--repo")
	if repo != "" {
		repo = pyPath(repo)
	} else {
		wd, err := unix.Getwd()
		if err != nil {
			fmt.Printf("error: %v\n", err)
			return 1, true
		}
		repo = wd
	}
	params := map[string]any{"task": tid, "repo": repo}
	if len(p.pos) == 2 {
		evidence := p.pos[1]
		if evidence == "-" {
			var why string
			if evidence, why = textArg(&evidence, "", "evidence"); why != "" {
				fmt.Println(why)
				return 1, true
			}
		}
		params["evidence"] = strings.TrimSpace(evidence) // a heredoc's last newline would keep a sha from reading as one
		if p.flags["--reason"] {
			params["reason"] = true
		}
	}
	if hasReq {
		params["request"] = request
	}
	if v, given := p.opt("--commit"); given {
		params["commit"] = v
	}
	if shots := p.each["--shot"]; len(shots) > 0 {
		wd, err := unix.Getwd()
		if err != nil {
			fmt.Printf("error: %v\n", err)
			return 1, true
		}
		abs := make([]string, len(shots)) // the daemon reads them from its own cwd (os.path.abspath)
		for i, s := range shots {
			if !filepath.IsAbs(s) {
				s = filepath.Join(wd, s)
			}
			abs[i] = filepath.Clean(s)
		}
		params["shots"] = abs
	}
	return said(c.Write("task.done", params)), true
}

func taskReopen(c *hlclient.Client, args []string) (int, bool) {
	return taskWithReason(c, args, "reopen", "reopen a task", "task.reopen", "note")
}

func taskDiscard(c *hlclient.Client, args []string) (int, bool) {
	return taskWithReason(c, args, "discard", "discard a task", "task.discard", "reason")
}

// taskWithReason is a subcommand taking T<n> and a reason, sent as key.
func taskWithReason(c *hlclient.Client, args []string, sub, verb, method, key string) (int, bool) {
	p, ok := parse(args, nil, nil, nil)
	if !ok || len(p.pos) != 2 {
		return 0, false
	}
	tid, ok := taskRef(p.pos[0])
	if !ok {
		return 0, false
	}
	mutating(c, sub)
	if !asking(c, verb) {
		return 1, true
	}
	return said(c.Write(method, map[string]any{"task": tid, key: p.pos[1]})), true
}

func taskAccept(c *hlclient.Client, args []string) (int, bool) {
	tid, ok := oneTask(args)
	if !ok {
		return 0, false
	}
	mutating(c, "accept")
	if !asking(c, "accept a task") {
		return 1, true
	}
	return said(c.Write("task.accept", map[string]any{"task": tid})), true
}

func taskReject(c *hlclient.Client, args []string) (int, bool) {
	return taskWithReason(c, args, "reject", "reject a task", "task.reject", "note")
}

func taskRelease(c *hlclient.Client, args []string) (int, bool) {
	return taskWithReason(c, args, "release", "release a task", "task.release", "reason")
}
