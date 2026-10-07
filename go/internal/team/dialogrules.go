package team

import (
	"fmt"
	"strings"

	"github.com/handline/handline/go/internal/protected"
	"github.com/handline/handline/go/internal/textsafe"
)

// Standing answers to a vendor's blocking prompts, on the founder's own say-so (handline/team/dialog_rules.py). A rule is
// {vendor, title, label}: for a prompt with exactly this title, choose the option whose label is exactly this. Never the option's
// number: a vendor reorders its options. Rules live in the protected store, so only the founder's session can add or remove one;
// an agent's session gets a refusal, not a raised error.
const (
	rulesNS   = "dialog_rules"
	rulesName = "rules.json"
)

// Rule is one standing answer.
type Rule struct{ Vendor, Title, Label string }

// DialogRules is the rules kept for home.
type DialogRules struct{ Store *protected.Store }

// NewDialogRules is the rules kept for home.
func NewDialogRules(home string) *DialogRules { return &DialogRules{protected.New(home)} }

// List is every rule, in order.
func (d *DialogRules) List() []Rule {
	raw, ok := d.Store.Get(rulesNS, rulesName)
	if !ok {
		return nil
	}
	v, err := decodeOrdered(raw)
	list, _ := v.([]any)
	if err != nil {
		return nil
	}
	var out []Rule
	for _, r := range list {
		o, ok := r.(*jobj)
		if !ok {
			continue
		}
		vendor, a := o.vals["vendor"].(string)
		title, b := o.vals["title"].(string)
		label, c := o.vals["label"].(string)
		if a && b && c && vendor != "" && title != "" && label != "" {
			out = append(out, Rule{vendor, title, label})
		}
	}
	return out
}

func problem(vendor, title, label string) string {
	for _, f := range [][2]string{{"vendor", vendor}, {"title", title}, {"label", label}} {
		if strings.TrimSpace(f[1]) == "" {
			return "a rule needs a " + f[0]
		}
		if textsafe.HasUnsafe(f[1]) {
			return "the " + f[0] + " holds a control character"
		}
	}
	return ""
}

func (d *DialogRules) save(rules []Rule) error {
	rows := make([]any, len(rules))
	for i, r := range rules {
		o := newJobj()
		o.set("vendor", r.Vendor)
		o.set("title", r.Title)
		o.set("label", r.Label)
		rows[i] = o
	}
	return d.Store.Put(rulesNS, rulesName, encodeIndent(rows))
}

// Add keeps a rule: (took, what to say).
func (d *DialogRules) Add(vendor, title, label string) (bool, string) {
	if bad := problem(vendor, title, label); bad != "" {
		return false, bad
	}
	rows := d.List()
	rule := Rule{vendor, title, label}
	for _, r := range rows {
		if r == rule {
			return true, fmt.Sprintf("already a rule: %s '%s' -> '%s'", vendor, title, label)
		}
	}
	for _, r := range rows {
		if r.Vendor == vendor && r.Title == title {
			return false, fmt.Sprintf("%s '%s' already has a rule; remove it before choosing another label", vendor, title)
		}
	}
	if err := d.save(append(rows, rule)); err != nil {
		return false, err.Error()
	}
	return true, fmt.Sprintf("%s '%s' will be answered '%s'", vendor, title, label)
}

// Match is the number of the option whose label exactly equals the rule for this vendor and title, or 0: no rule, no such label
// among the options (numbers[i] is labels[i]'s number), or two options with that label (which one is not knowable).
func (d *DialogRules) Match(vendor, title string, numbers []int, labels []string) int {
	for _, r := range d.List() {
		if r.Vendor == vendor && r.Title == title {
			hit, n := 0, 0
			for i, l := range labels {
				if l == r.Label {
					hit, n = numbers[i], n+1
				}
			}
			if n != 1 {
				return 0
			}
			return hit
		}
	}
	return 0
}
