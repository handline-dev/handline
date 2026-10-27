package status

import (
	"encoding/json"
	"reflect"
	"sort"
	"testing"

	"github.com/handline/handline/go/internal/contracttest"
)

func TestTheWordsAreTheContracts(t *testing.T) {
	raw := contracttest.Spec(t)
	var spec struct {
		Status struct {
			States  map[string]string `json:"states"`
			Reasons map[string]string `json:"reasons"`
		} `json:"x-status"`
	}
	if err := json.Unmarshal(raw, &spec); err != nil {
		t.Fatal(err)
	}
	for _, c := range []struct {
		name string
		ours []string
		spec map[string]string
	}{{"states", States, spec.Status.States}, {"reasons", Reasons, spec.Status.Reasons}} {
		var theirs []string
		for w := range c.spec {
			theirs = append(theirs, w)
		}
		ours := append([]string(nil), c.ours...)
		sort.Strings(ours)
		sort.Strings(theirs)
		if !reflect.DeepEqual(ours, theirs) {
			t.Errorf("%s: ours %v, the contract's %v", c.name, ours, theirs)
		}
	}
}
