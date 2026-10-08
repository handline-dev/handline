package tick

import (
	"fmt"
	"sort"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/db"
)

// The wait-for graph over task_blocks: what can never finish, told to the human once. A task waits on the tasks in task_blocks (a
// blocked-by, or the block a child task gives its parent). Two shapes are stuck for good, both read from the db every tick, not caught at
// the moment they arise: a cycle (add_block refuses one, but a merge or a reopen can still close a loop), and a wait on a blocker that was
// discarded, which stops blocking, so the waiter quietly proceeds without what it waited for. Each goes to the founder once, as a decision,
// and is remembered by a cursor until the shape is gone. Nothing is fixed and no agent is told: which edge to drop is the founder's call.
const waitforPrefix = "waitfor:"

// Decider is team/ask.py's raise_decision.
type Decider interface {
	RaiseDecision(q db.Q, who, text, source string) error
}

// StuckWait is the task to name the decision after and the text, under the fingerprint that tells one shape from another.
type StuckWait struct {
	FP   string
	Task int64
	Text string
}

// stuckSet keeps the order the waits were found in, as Python's dict does.
type stuckSet struct {
	list []StuckWait
	at   map[string]int
}

func (s *stuckSet) put(w StuckWait) {
	if i, ok := s.at[w.FP]; ok {
		s.list[i] = w
		return
	}
	s.at[w.FP] = len(s.list)
	s.list = append(s.list, w)
}

func open(t *db.Task) bool { return !t.Ended() }

// cycles are the strongly connected groups of more than one task (or one waiting on itself), each as a sorted slice of task ids. Tarjan's,
// iterative so a long chain cannot overflow the stack.
func cycles(edges map[int64][]int64) [][]int64 {
	index, low := map[int64]int{}, map[int64]int{}
	on := map[int64]bool{}
	var stack []int64
	var out [][]int64
	counter := 0
	roots := make([]int64, 0, len(edges))
	for r := range edges {
		roots = append(roots, r)
	}
	sort.Slice(roots, func(i, j int) bool { return roots[i] < roots[j] })
	type frame struct {
		node int64
		next int
	}
	for _, root := range roots {
		if _, seen := index[root]; seen {
			continue
		}
		work := []frame{{root, 0}}
		index[root], low[root] = counter, counter
		counter++
		stack = append(stack, root)
		on[root] = true
		for len(work) > 0 {
			f := &work[len(work)-1]
			node := f.node
			descended := false
			for f.next < len(edges[node]) {
				nxt := edges[node][f.next]
				f.next++
				if _, seen := index[nxt]; !seen {
					index[nxt], low[nxt] = counter, counter
					counter++
					stack = append(stack, nxt)
					on[nxt] = true
					work = append(work, frame{nxt, 0})
					descended = true
					break
				}
				if on[nxt] && index[nxt] < low[node] {
					low[node] = index[nxt]
				}
			}
			if descended {
				continue
			}
			work = work[:len(work)-1]
			if len(work) > 0 {
				parent := work[len(work)-1].node
				if low[node] < low[parent] {
					low[parent] = low[node]
				}
			}
			if low[node] == index[node] {
				var group []int64
				for {
					member := stack[len(stack)-1]
					stack = stack[:len(stack)-1]
					delete(on, member)
					group = append(group, member)
					if member == node {
						break
					}
				}
				self := false
				for _, n := range edges[node] {
					if n == node {
						self = true
					}
				}
				if len(group) > 1 || self {
					sort.Slice(group, func(i, j int) bool { return group[i] < group[j] })
					out = append(out, group)
				}
			}
		}
	}
	return out
}

// ring is one loop through the group's smallest task, in the order the waits go: the shortest way from it back to it along waits inside the
// group.
func ring(edges map[int64][]int64, group []int64) []int64 {
	start := group[0]
	inside := map[int64]bool{}
	for _, g := range group {
		inside[g] = true
	}
	queue := []int64{start}
	came := map[int64]*int64{start: nil}
	for len(queue) > 0 {
		node := queue[0]
		queue = queue[1:]
		for _, nxt := range edges[node] {
			if nxt == start {
				loop := []int64{node}
				for p := came[loop[len(loop)-1]]; p != nil; p = came[loop[len(loop)-1]] {
					loop = append(loop, *p)
				}
				for i, j := 0, len(loop)-1; i < j; i, j = i+1, j-1 {
					loop[i], loop[j] = loop[j], loop[i]
				}
				return loop
			}
			if _, seen := came[nxt]; inside[nxt] && !seen {
				n := node
				came[nxt] = &n
				queue = append(queue, nxt)
			}
		}
	}
	return group
}

// StuckWaits is {fingerprint: the stuck wait} for every stuck wait now.
func StuckWaits(q db.Q) ([]StuckWait, error) {
	all, err := db.AllTasks(q)
	if err != nil {
		return nil, err
	}
	tasks := map[int64]*db.Task{}
	for _, t := range all {
		tasks[t.ID] = t
	}
	rows, err := q.Query("SELECT task_id, blocks_on FROM task_blocks ORDER BY task_id, blocks_on")
	if err != nil {
		return nil, err
	}
	type pair struct{ waiter, blocker int64 }
	var pairs []pair
	for rows.Next() {
		var p pair
		if err := rows.Scan(&p.waiter, &p.blocker); err != nil {
			rows.Close()
			return nil, err
		}
		pairs = append(pairs, p)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return nil, err
	}
	edges := map[int64][]int64{}
	found := &stuckSet{at: map[string]int{}}
	for _, p := range pairs {
		waiter, blocker := tasks[p.waiter], tasks[p.blocker]
		if waiter == nil || !open(waiter) {
			continue
		}
		if blocker == nil || blocker.DiscardedBy.String != "" {
			gone := "does not exist"
			if blocker != nil {
				gone = "was discarded by " + blocker.DiscardedBy.String
			}
			found.put(StuckWait{fmt.Sprintf("gone:%d>%d", waiter.ID, p.blocker), waiter.ID, fmt.Sprintf(
				"T%d (%s) waited on T%d, which %s. That wait no longer holds it back, but what it waited for never happened - "+
					"check whether T%d can go on, or say what it needs instead.", waiter.ID, waiter.Owner, p.blocker, gone, waiter.ID)})
		} else if open(blocker) {
			edges[waiter.ID] = append(edges[waiter.ID], blocker.ID)
		}
	}
	for _, group := range cycles(edges) {
		loop := ring(edges, group)
		var names []string
		for _, id := range append(append([]int64{}, loop...), loop[0]) {
			names = append(names, fmt.Sprintf("T%d (%s)", id, tasks[id].Owner))
		}
		var more, ids []string
		inLoop := map[int64]bool{}
		for _, id := range loop {
			inLoop[id] = true
		}
		for _, id := range group {
			ids = append(ids, strconv.FormatInt(id, 10))
			if !inLoop[id] {
				more = append(more, strconv.FormatInt(id, 10))
			}
		}
		text := "These tasks wait on each other and none can finish: " + strings.Join(names, " -> ")
		if len(more) > 0 {
			text += ", and T" + strings.Join(more, ", T") + " in the same tangle"
		}
		text += ". Drop one wait (`hl task edit T<n> --unblock T<m>`, T<n> being the one that waits) or close one of them."
		found.put(StuckWait{"cycle:" + strings.Join(ids, "-"), group[0], text})
	}
	return found.list, nil
}

// WaitFor raises each stuck wait to the founder once and forgets the ones that are gone.
type WaitFor struct {
	Decide Decider
	Log    func(string)
}

// Sweep is waitfor.sweep.
func (w *WaitFor) Sweep(q db.Q, now int64) error {
	current, err := StuckWaits(q)
	if err != nil {
		return err
	}
	rows, err := q.Query("SELECT name FROM cursors WHERE name LIKE ?", waitforPrefix+"%")
	if err != nil {
		return err
	}
	told := map[string]bool{}
	var toldNames []string
	for rows.Next() {
		var n string
		if err := rows.Scan(&n); err != nil {
			rows.Close()
			return err
		}
		told[n] = true
		toldNames = append(toldNames, n)
	}
	rows.Close()
	present := map[string]bool{}
	for _, s := range current {
		present[s.FP] = true
		fp := s.FP
		if told[waitforPrefix+fp] {
			continue
		}
		task, err := db.GetTask(q, s.Task)
		if err != nil {
			return err
		}
		who := db.Human
		for _, o := range []string{task.Owner, task.OpenedBy} {
			if o != db.Human {
				who = o
				break
			}
		}
		if err := w.Decide.RaiseDecision(q, who, "[wait-for] "+s.Text, "wait-for graph"); err != nil {
			return err
		}
		if err := db.SetCursor(q, waitforPrefix+fp, 1); err != nil {
			return err
		}
		w.Log(fmt.Sprintf("  wait-for: %s raised to the founder", fp))
	}
	for _, name := range toldNames {
		if !present[name[len(waitforPrefix):]] {
			if err := db.ClearCursor(q, name); err != nil {
				return err
			}
		}
	}
	return nil
}
