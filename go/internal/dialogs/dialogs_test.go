package dialogs

import (
	"encoding/json"
	"os"
	"testing"
)

// The table is the contract's x-dialogs: same dialogs, same class, same kind.
func TestTheTableIsTheContractsTable(t *testing.T) {
	raw, err := os.ReadFile("../../../contract/protocol.json")
	if err != nil {
		t.Fatal(err)
	}
	var spec struct {
		X struct {
			Dialogs map[string]struct {
				Class string `json:"class"`
				Kind  string `json:"kind"`
			} `json:"dialogs"`
		} `json:"x-dialogs"`
	}
	if err := json.Unmarshal(raw, &spec); err != nil {
		t.Fatal(err)
	}
	if len(spec.X.Dialogs) != len(Table) {
		t.Fatalf("the contract names %d dialogs, the table %d", len(spec.X.Dialogs), len(Table))
	}
	for id, c := range spec.X.Dialogs {
		if got, ok := Table[id]; !ok || got.Class != c.Class || got.Kind != c.Kind {
			t.Errorf("%s: contract %+v, table %+v", id, c, got)
		}
	}
}

func TestOnlyARoutineDialogIsAnsweredAndAnUnknownOneIsAPersons(t *testing.T) {
	for id, want := range map[string]bool{"folder_trust": true, "bypass_warning": true, "tool_permission": false, "question": false,
		"usage_limit_choice": false, "exit_confirm": false, "startup_notice": false, "a dialog from the next release": false, "": false} {
		if Answered(id) != want {
			t.Errorf("%q answered: %v", id, !want)
		}
	}
	if e := Of("a dialog from the next release"); e.Class != Unknown || e.Kind != Dialog {
		t.Errorf("%+v", e)
	}
}
