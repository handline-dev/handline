package team

import (
	"strings"
	"testing"
)

func TestDialogRulesAreKeptOnceAndAnotherLabelForTheSameTitleIsRefused(t *testing.T) {
	d := NewDialogRules(newHome(t))
	if len(d.List()) != 0 {
		t.Fatal("rules from nowhere")
	}
	if ok, said := d.Add("claude", "Overwrite the file?", "Yes"); !ok || said != "claude 'Overwrite the file?' will be answered 'Yes'" {
		t.Fatalf("%v %q", ok, said)
	}
	if ok, said := d.Add("claude", "Overwrite the file?", "Yes"); !ok || !strings.HasPrefix(said, "already a rule") {
		t.Fatalf("%v %q", ok, said)
	}
	if ok, said := d.Add("claude", "Overwrite the file?", "No"); ok || !strings.Contains(said, "already has a rule") {
		t.Fatalf("%v %q", ok, said)
	}
	if ok, _ := d.Add("opencode", "Overwrite the file?", "No"); !ok {
		t.Fatal("a rule is per vendor")
	}
	raw, _ := d.Store.Get(rulesNS, rulesName)
	if !strings.HasPrefix(raw, "[\n  {\n    \"vendor\": \"claude\",") {
		t.Fatalf("the rules are the Python's indent-2 JSON: %s", raw)
	}
	if got := d.List(); len(got) != 2 || got[0] != (Rule{"claude", "Overwrite the file?", "Yes"}) {
		t.Fatalf("%v", got)
	}
}

func TestADialogRuleNeedsAllThreeFieldsAndNoControlCharacter(t *testing.T) {
	d := NewDialogRules(newHome(t))
	for _, c := range []struct{ vendor, title, label, want string }{
		{"", "T", "L", "a rule needs a vendor"},
		{"v", " ", "L", "a rule needs a title"},
		{"v", "T", "", "a rule needs a label"},
		{"v", "T\x1b[2J", "L", "the title holds a control character"},
	} {
		if ok, said := d.Add(c.vendor, c.title, c.label); ok || said != c.want {
			t.Fatalf("%+v -> %v %q", c, ok, said)
		}
	}
	if len(d.List()) != 0 {
		t.Fatal("a refused rule was kept")
	}
}

func TestADialogRuleMatchesByExactLabelNeverByNumber(t *testing.T) {
	d := NewDialogRules(newHome(t))
	d.Add("claude", "Overwrite?", "Yes")
	if n := d.Match("claude", "Overwrite?", []int{1, 2}, []string{"No", "Yes"}); n != 2 {
		t.Fatalf("the label moved to 2: %d", n)
	}
	for name, got := range map[string]int{
		"no such label":      d.Match("claude", "Overwrite?", []int{1, 2}, []string{"No", "Maybe"}),
		"two with the label": d.Match("claude", "Overwrite?", []int{1, 2}, []string{"Yes", "Yes"}),
		"another title":      d.Match("claude", "Delete?", []int{1}, []string{"Yes"}),
		"another vendor":     d.Match("opencode", "Overwrite?", []int{1}, []string{"Yes"}),
	} {
		if got != 0 {
			t.Fatalf("%s: %d", name, got)
		}
	}
}

func TestAnAgentSessionCannotWriteTheRules(t *testing.T) {
	d := NewDialogRules(newHome(t))
	t.Setenv("OCP_AGENT", "backend")
	t.Setenv("HANDLINE_AGENT", "backend")
	if ok, said := d.Add("claude", "Overwrite?", "Yes"); ok || said == "" {
		t.Fatalf("%v %q", ok, said)
	}
	t.Setenv("OCP_AGENT", "")
	if len(d.List()) != 0 {
		t.Fatal("an agent wrote the founder's rules")
	}
}
