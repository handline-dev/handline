// Package handoff is the session's record of itself: the digest a fold keeps, the mechanical handoff record, and the agent's own
// handoff note (handline/session/digest.py, handoff.py and daemon/handoff_note.py).
package handoff

import (
	"fmt"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/textsafe"
)

// Digest parsing, validation and rendering. The section list is fixed on purpose. With fixed sections a fold becomes "move items
// between sections", which is a stable rewrite; free-form prose makes the structure wobble on every pass. Section headings are a
// data format that agents and the user both read, so they are fixed. Digests written before the headings were English carry the
// older Korean ones; ParseDigest reads both and MigrateText rewrites them.

// Section is a digest section: its identifier and the heading as it appears in the file.
type Section struct{ Key, Heading string }

// Sections is the fixed list, in file order.
var Sections = []Section{
	{"done", "Done"}, {"in_progress", "In progress"}, {"decided", "Decided"}, {"blocked", "Blocked"}, {"next", "Next"},
	{"base_candidates", "Base candidates"},
}

// LegacyHeadings are the headings digests had before they were English, by what they became.
var LegacyHeadings = map[string]string{"완료": "Done", "진행중": "In progress", "결정": "Decided", "막힌 것": "Blocked",
	"다음": "Next", "base 승격 후보": "Base candidates", "기계 기록": "Machine record", "지각 반영": "Late additions"}

// FactsHeading is appended by us after every fold, independent of anything a model produced, so a failed fold still leaves these
// facts in the digest.
const (
	FactsHeading = "Machine record"
	LateHeading  = "Late additions"
)

var byHeading = func() map[string]string {
	m := map[string]string{}
	for _, s := range Sections {
		m[s.Heading] = s.Key
	}
	for old, now := range LegacyHeadings {
		if k, ok := m[now]; ok {
			m[old] = k
		}
	}
	return m
}()

const (
	sp = `[` + textsafe.Space + `]`
	ns = `[^` + textsafe.Space + `]`
)

var (
	h2RE   = regexp.MustCompile(`(?m)^##` + sp + `+(.+?)` + sp + `*$`)
	itemRE = regexp.MustCompile(`(?m)^` + sp + `*[-*]` + sp + `+(.*` + ns + `)` + sp + `*$`)
	headRE = regexp.MustCompile(`(?m)^#` + sp + `+(` + ns + `+)` + sp + `*·` + sp + `*(` + ns + `+)`)
	metaRE = regexp.MustCompile(`(?m)^updated:` + sp + `*(.*?)(?:` + sp + `*·` + sp + `*folded_through:` + sp + `*event` + sp + `*([0-9]+))?` + sp + `*$`)
)

// Digest is a day's digest of one agent.
type Digest struct {
	Date, Agent, Updated string
	FoldedThrough        int
	Sections             map[string][]string
	Facts                string
	Late                 []string
}

// Items are the items under a section key.
func (d *Digest) Items(key string) []string { return d.Sections[key] }

func items(body string) []string {
	out := []string{}
	for _, m := range itemRE.FindAllStringSubmatch(body, -1) {
		out = append(out, textsafe.Strip(m[1]))
	}
	return out
}

func emptySections() map[string][]string {
	m := map[string][]string{}
	for _, s := range Sections {
		m[s.Key] = []string{}
	}
	return m
}

// ParseDigest reads a digest file.
func ParseDigest(text string) *Digest {
	d := &Digest{Sections: emptySections()}
	if m := headRE.FindStringSubmatch(text); m != nil {
		d.Date, d.Agent = m[1], m[2]
	}
	if m := metaRE.FindStringSubmatch(text); m != nil {
		d.Updated = textsafe.Strip(m[1])
		if m[2] != "" {
			d.FoldedThrough, _ = strconv.Atoi(m[2])
		}
	}
	// Split on level-2 headings and bucket the items under each.
	marks := h2RE.FindAllStringSubmatchIndex(text, -1)
	for i, m := range marks {
		heading := textsafe.Strip(text[m[2]:m[3]])
		end := len(text)
		if i+1 < len(marks) {
			end = marks[i+1][0]
		}
		body := text[m[1]:end]
		if key, ok := byHeading[heading]; ok {
			d.Sections[key] = items(body)
		} else if heading == FactsHeading || heading == "기계 기록" {
			d.Facts = textsafe.Strip(body)
		} else if heading == LateHeading || heading == "지각 반영" {
			d.Late = items(body)
		}
	}
	return d
}

// MigrateText is text with its Korean section headings in English. Only a line that is a heading and nothing else is touched, so
// an item that happens to contain one of the words is left as it was.
func MigrateText(text string) string {
	return h2RE.ReplaceAllStringFunc(text, func(s string) string {
		h := textsafe.Strip(h2RE.FindStringSubmatch(s)[1])
		if now, ok := LegacyHeadings[h]; ok {
			h = now
		}
		return "## " + h
	})
}

// Render is the file for a digest.
func (d *Digest) Render() string {
	out := []string{fmt.Sprintf("# %s · %s", d.Date, d.Agent), fmt.Sprintf("updated: %s · folded_through: event %d", d.Updated, d.FoldedThrough), ""}
	for _, s := range Sections {
		out = append(out, "## "+s.Heading)
		its := d.Sections[s.Key]
		for _, i := range its {
			out = append(out, "- "+i)
		}
		if len(its) == 0 {
			out = append(out, "")
		}
		out = append(out, "")
	}
	if len(d.Late) > 0 {
		out = append(out, "## "+LateHeading)
		for _, i := range d.Late {
			out = append(out, "- "+i)
		}
		out = append(out, "")
	}
	if d.Facts != "" {
		out = append(out, "## "+FactsHeading, textsafe.Strip(d.Facts), "")
	}
	return strings.TrimRightFunc(strings.Join(out, "\n"), textsafe.IsSpace) + "\n"
}

// Validate checks a candidate digest before it is allowed to replace a good one. Atomic writes only stop a half-written file; they
// do not stop a fully written garbage file from overwriting a good one, which is what these checks are for.
func Validate(text string, previous *Digest, headerIsStamped bool) (bool, []string) {
	var problems []string
	found := map[string]bool{}
	for _, m := range h2RE.FindAllStringSubmatch(text, -1) {
		h := textsafe.Strip(m[1])
		if now, ok := LegacyHeadings[h]; ok {
			h = now
		}
		found[h] = true
	}
	var missing []string
	for _, s := range Sections {
		if !found[s.Heading] {
			missing = append(missing, s.Heading)
		}
	}
	if len(missing) > 0 {
		problems = append(problems, "missing sections: "+strings.Join(missing, ", "))
	}
	cand := ParseDigest(text)
	if cand.Agent == "" && !headerIsStamped {
		// The caller that stamps the header does not care what the model wrote there. Rejecting on it throws away good section
		// work, takes the degraded path and leaves the cursor unadvanced, all over a line that was about to be replaced.
		problems = append(problems, "header line '# <date> · <agent>' is missing or malformed")
	}
	if previous != nil {
		// Monotonicity: nothing that was already done may disappear. A fold is a destructive rewrite, so losing history is the
		// failure to catch.
		after := map[string]bool{}
		for _, i := range cand.Items("done") {
			after[textsafe.Strip(i)] = true
		}
		seen := map[string]bool{}
		var lost []string
		for _, i := range previous.Items("done") {
			i = textsafe.Strip(i)
			if !after[i] && !seen[i] {
				seen[i] = true
				lost = append(lost, i)
			}
		}
		if len(lost) > 0 {
			sort.Strings(lost)
			problems = append(problems, fmt.Sprintf("%d completed item(s) disappeared: %s", len(lost), strings.Join(lost[:min(3, len(lost))], "; ")))
		}
		if previous.FoldedThrough != 0 && cand.FoldedThrough < previous.FoldedThrough {
			problems = append(problems, fmt.Sprintf("folded_through went backwards: %d -> %d", previous.FoldedThrough, cand.FoldedThrough))
		}
	}
	return len(problems) == 0, problems
}

// CarryForward is the midnight rollover, with no model involved. Yesterday's "in progress" would otherwise stay in progress
// forever. Carry in_progress / blocked / next; start "done" empty for the new day.
func CarryForward(previous *Digest, newDate string) *Digest {
	d := &Digest{Date: newDate, Agent: previous.Agent, Updated: "00:00 (rollover)", FoldedThrough: previous.FoldedThrough, Sections: emptySections()}
	for _, k := range []string{"in_progress", "blocked", "next", "base_candidates"} {
		d.Sections[k] = append([]string{}, previous.Items(k)...)
	}
	return d
}
