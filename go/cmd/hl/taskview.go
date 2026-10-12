package main

import (
	"errors"
	"fmt"
	"math"
	"sort"
	"strings"
	"time"

	"github.com/handline/handline/go/internal/hlclient"
	"github.com/handline/handline/go/internal/taskresult"
	"github.com/handline/handline/go/internal/textsafe"
	"github.com/handline/handline/go/internal/timefmt"
)

// The wire's JSON as decoded: an object's members, read loosely the way the Python client reads them.

type obj = map[string]any

func str(o obj, k string) string {
	switch v := o[k].(type) {
	case string:
		return v
	case nil:
		return ""
	default:
		return fmt.Sprint(v)
	}
}

func num(o obj, k string) int64 {
	f, _ := o[k].(float64)
	return int64(f)
}

func list(o obj, k string) []obj {
	items, _ := o[k].([]any)
	out := make([]obj, 0, len(items))
	for _, it := range items {
		if m, ok := it.(obj); ok {
			out = append(out, m)
		}
	}
	return out
}

func given(o obj, k string) bool { v, ok := o[k]; return ok && v != nil && v != "" && v != false }

// epoch is a wire Timestamp as seconds, 0 for none (cli/client.epoch, then timefmt.stamp's `ts or 0`).
func epoch(ts any) int64 {
	s, _ := ts.(string)
	t, err := time.Parse(time.RFC3339, s)
	if err != nil {
		return 0
	}
	return t.Unix()
}

func stamp(ts any) string { return timefmt.Stamp(epoch(ts), timefmt.DateTime) }

// readFailed prints why a read failed the way the Python commands do: a refusal as it is, else `could not read <what>`.
func readFailed(err error, what string) int {
	var refused *hlclient.RPCError
	if errors.As(err, &refused) {
		fmt.Println(refused.Error())
	} else {
		fmt.Printf("could not read %s: %v\n", what, err)
	}
	return 1
}

func memoList(c *hlclient.Client, tid int) int {
	t, err := c.Call("task.get", obj{"task": tid})
	if err != nil {
		return readFailed(err, fmt.Sprintf("T%d", tid))
	}
	memos := list(t, "memos")
	for _, m := range memos {
		edited := ""
		if given(m, "edited_at") {
			edited = ", edited " + stamp(m["edited_at"])
		}
		fmt.Printf("memo %s  %s  %s%s\n  %s\n", str(m, "id"), str(m, "author"), stamp(m["at"]), edited, str(m, "text"))
	}
	if len(memos) == 0 {
		fmt.Printf("T%d has no memos\n", tid)
	}
	return 0
}

func taskShow(c *hlclient.Client, args []string) (int, bool) {
	tid, ok := oneTask(args)
	if !ok {
		return 0, false
	}
	t, err := c.Call("task.get", obj{"task": tid})
	if err != nil {
		return readFailed(err, fmt.Sprintf("T%d", tid)), true
	}
	// Titles, briefs and notes are other agents' text.
	fmt.Println(textsafe.Text(strings.Join(detailLines(t, float64(time.Now().UnixNano())/1e9, c.Home), "\n")))
	return 0, true
}

// detailLines is one task as lines to read (cli/task._detail_lines); home is where the daemon keeps the screenshots.
func detailLines(t obj, now float64, home string) []string {
	reporter := ""
	if a := str(t, "asker"); a != "" && a != str(t, "from") {
		reporter = "   reporter " + a
	}
	head := fmt.Sprintf("state %s   owner %s   opened by %s%s   P%d", str(t, "state"), str(t, "to"), str(t, "from"), reporter, num(t, "priority"))
	if given(t, "parent") {
		head += fmt.Sprintf("   parent T%d", num(t, "parent"))
	}
	out := []string{fmt.Sprintf("T%d  %s", num(t, "id"), str(t, "title")), head + "   created " + stamp(t["created"])}
	if box, ok := t["time"].(obj); ok {
		out = append(out, timeoutLines(box, now)...)
	}
	if given(t, "evidence") {
		out = append(out, fmt.Sprintf("done %s: %s", stamp(t["done"]), str(t, "evidence")))
	}
	if r, ok := t["result"].(obj); ok {
		out = append(out, resultLines(r, num(t, "id"), home)...)
	}
	if d, ok := t["discarded"].(obj); ok {
		reason := str(d, "reason")
		if reason == "" {
			reason = "no reason given"
		}
		out = append(out, fmt.Sprintf("discarded by %s: %s", str(d, "by"), reason))
	}
	body := str(t, "body")
	if body == "" {
		body = "(none)"
	}
	out = append(out, "", "brief", body)
	if memos := list(t, "memos"); len(memos) > 0 {
		out = append(out, "", "memos")
		for _, m := range memos {
			out = append(out, fmt.Sprintf("  memo %s  %s  %s\n    %s", str(m, "id"), str(m, "author"), stamp(m["at"]), str(m, "text")))
		}
	}
	if bs := list(t, "blockers"); len(bs) > 0 {
		out = append(out, "", "waiting on")
		for _, b := range bs {
			out = append(out, fmt.Sprintf("  T%d (%s)  %s", num(b, "task"), str(b, "owner"), str(b, "title")))
		}
	}
	if ks := list(t, "subtasks"); len(ks) > 0 {
		out = append(out, "", "child tasks")
		for _, k := range ks {
			out = append(out, fmt.Sprintf("  T%d (%s, %s)  %s", num(k, "id"), str(k, "owner"), str(k, "state"), str(k, "title")))
		}
	}
	if hs := list(t, "handoffs"); len(hs) > 0 {
		out = append(out, "", "handoffs")
		for _, h := range hs {
			out = append(out, fmt.Sprintf("  %s  %s -> %s\n    %s", stamp(h["at"]), str(h, "from"), str(h, "to"), str(h, "note")))
		}
	}
	return out
}

// resultLines are what the task's end kept: the commit and whether main reaches it, and the screenshots where the daemon keeps
// them (shots/T<n>/<k>.png under its home).
func resultLines(r obj, tid int64, home string) []string {
	var out []string
	if given(r, "commit") {
		where := "not on main"
		if r["on_main"] == true {
			where = "on main"
		}
		out = append(out, fmt.Sprintf("commit %s (%s)", str(r, "commit"), where))
	}
	for _, s := range list(r, "shots") {
		out = append(out, fmt.Sprintf("shot %s (%d bytes)", taskresult.ShotFile(home, tid, int(num(s, "n"))), num(s, "bytes")))
	}
	return out
}

// timeoutLines is the wire's `time` as the task page's lines (cli/task._clock, then actions.timeout_lines).
func timeoutLines(box obj, now float64) []string {
	line := "timeout " + timefmt.Span(num(box, "length_s"), false)
	if box["default"] == true {
		line += " (the default)"
	}
	if !given(box, "start") {
		return []string{line + " - counts while the task is doing"}
	}
	second := fmt.Sprintf("counting since %s   ends %s", stamp(box["start"]), stamp(box["end"]))
	if given(box, "overdue_since") {
		second += "   over by " + timefmt.Span(int64(math.Trunc(now-float64(epoch(box["overdue_since"])))), false)
		if told := num(box, "told"); told > 0 {
			second += fmt.Sprintf(", opener told %dx", told)
		}
	} else if given(box, "end") {
		second += "   " + timefmt.Span(int64(math.Trunc(float64(epoch(box["end"]))-now)), false) + " left"
	}
	return []string{line, second}
}

func taskList(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, []string{"--owner", "--state"}, []string{"--all", "--everyone"}, nil)
	if !ok || len(p.pos) != 0 {
		return 0, false
	}
	params := obj{"all": p.flags["--all"], "everyone": p.flags["--everyone"], "limit": 200}
	owner, hasOwner := p.opt("--owner")
	if hasOwner {
		params["owner"] = owner
	}
	if state, given := p.opt("--state"); given {
		switch state {
		case "draft", "queued", "doing", "done", "discarded":
			params["state"] = state
		default:
			return 0, false
		}
	}
	var rows []obj
	var gaps obj
	for {
		params["reason_gaps"] = gaps == nil
		page, err := c.Call("tasks.list", params)
		var refused *hlclient.RPCError
		if errors.As(err, &refused) {
			fmt.Println(refused.Error())
			if hasOwner && !p.flags["--everyone"] {
				fmt.Println("`hl task list --everyone` shows every team's tasks")
			}
			return 1, true
		}
		if err != nil {
			fmt.Printf("could not read the tasks: %v\n", err)
			return 1, true
		}
		items := list(page, "items")
		rows = append(rows, items...)
		if gaps == nil {
			gaps, _ = page["reason_gaps"].(obj)
		}
		if page["more"] != true || len(items) == 0 {
			break
		}
		params["before"] = items[len(items)-1]["id"]
	}
	sort.SliceStable(rows, func(i, j int) bool {
		if a, b := num(rows[i], "priority"), num(rows[j], "priority"); a != b {
			return a < b
		}
		return num(rows[i], "id") < num(rows[j], "id")
	})
	printBoard(rows)
	fmt.Printf("reason (last 30d): %d/%d retirements carry no reason, %d closures by sha, %d by request, %d by reason\n",
		num(gaps, "retirements_blank"), num(gaps, "retirements"), num(gaps, "closures_sha"),
		num(gaps, "closures_request"), num(gaps, "closures_reason"))
	return 0, true
}

// printBoard is each task, then the requests linked to it as they stand now (cli/task._print_board).
func printBoard(tasks []obj) {
	if len(tasks) == 0 {
		fmt.Println("no open tasks.")
		return
	}
	one := textsafe.Oneline
	for _, t := range tasks {
		var b strings.Builder
		fmt.Fprintf(&b, "%-5s %s  [%s] %s", fmt.Sprintf("T%d", num(t, "id")), one(str(t, "title")), str(t, "to"), str(t, "state"))
		if given(t, "parent") {
			fmt.Fprintf(&b, " (under T%d)", num(t, "parent"))
		}
		if given(t, "edited_by") {
			b.WriteString("  edited by " + one(str(t, "edited_by")))
		}
		if given(t, "evidence") {
			b.WriteString("  DONE: " + one(str(t, "evidence")))
		}
		if gone, ok := t["discarded"].(obj); ok {
			fmt.Fprintf(&b, "  DISCARDED by %s: %s", one(str(gone, "by")), one(str(gone, "reason")))
		}
		fmt.Fprintf(&b, "  P%d  may reopen: %s", num(t, "priority"), str(t, "asker"))
		if given(t, "block_reason") {
			b.WriteString("  " + str(t, "block_reason"))
		}
		b.WriteString("  active " + stamp(t["last_activity"]))
		fmt.Println(b.String())
		if given(t, "mismatch") {
			fmt.Println("        ! " + str(t, "mismatch"))
		}
		if given(t, "hold_reason") {
			fmt.Println("        " + str(t, "hold_reason"))
		}
		for _, r := range list(t, "requests") {
			fmt.Printf("        request #%s %s → %s %s %s\n", str(r, "id"), str(r, "from"), str(r, "to"), str(r, "state"), stamp(r["at"]))
		}
	}
}

func taskBoard(c *hlclient.Client, args []string) (int, bool) {
	p, ok := parse(args, nil, []string{"--everyone"}, nil)
	if !ok || len(p.pos) != 0 {
		return 0, false
	}
	if !asking(c, "board") {
		return 1, true
	}
	board, err := c.Call("task.board", obj{"everyone": p.flags["--everyone"]})
	if err != nil {
		fmt.Printf("could not read the board: %v\n", err)
		return 1, true
	}
	rows := append(list(board, "mine"), list(board, "reports")...)
	key := func(r obj) (int64, int64) { t, _ := r["task"].(obj); return num(t, "priority"), num(t, "id") }
	sort.SliceStable(rows, func(i, j int) bool {
		pi, ii := key(rows[i])
		pj, ij := key(rows[j])
		if pi != pj {
			return pi < pj
		}
		return ii < ij
	})
	if len(rows) == 0 {
		fmt.Println("no open tasks.")
		return 0, true
	}
	for _, r := range rows {
		t, _ := r["task"].(obj)
		now, _ := r["now"].(obj)
		parent := ""
		if given(t, "parent") {
			parent = fmt.Sprintf(" (under T%d)", num(t, "parent"))
		}
		fmt.Println(fit100(fmt.Sprintf("%-5s %s%s", fmt.Sprintf("T%d", num(t, "id")), textsafe.Oneline(str(t, "title")), parent)))
		fmt.Println(fit100("      now: " + nowSaid(now)))
		if given(t, "hold_reason") {
			fmt.Println(fit100("      " + str(t, "hold_reason")))
		}
		if given(t, "mismatch") {
			fmt.Println(fit100("      ! " + str(t, "mismatch")))
		}
	}
	return 0, true
}

// nowSaid is who holds a task now, in words (cli/task.cmd_task_board).
func nowSaid(now obj) string {
	var tasks []string
	for _, n := range anyList(now["tasks"]) {
		tasks = append(tasks, fmt.Sprintf("T%v", n))
	}
	var agents []string
	for _, a := range anyList(now["agents"]) {
		agents = append(agents, fmt.Sprint(a))
	}
	switch str(now, "kind") {
	case "waiting":
		return "waiting on " + strings.Join(tasks, ", ")
	case "held":
		return strings.Join(agents, ", ")
	case "sub_task":
		return "sub-task: " + strings.Join(agents, ", ")
	case "held_as_sub_task":
		var pairs []string
		for i := 0; i < len(tasks) && i < len(agents); i++ {
			pairs = append(pairs, fmt.Sprintf("%s (%s)", tasks[i], agents[i]))
		}
		return "held as sub-task " + strings.Join(pairs, ", ")
	case "result_in":
		return "you - a result is in"
	}
	return "nobody holds it"
}

func anyList(v any) []any {
	out, _ := v.([]any)
	for i, x := range out {
		if f, ok := x.(float64); ok && f == math.Trunc(f) {
			out[i] = int64(f)
		}
	}
	return out
}

// fit100 is one screen wide: plain text past 100 characters is cut with an ellipsis.
func fit100(line string) string {
	r := []rune(line)
	if len(r) <= 100 {
		return line
	}
	return string(r[:99]) + "…"
}
