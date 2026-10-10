// Package dialogs is the table of dialogs an agent's terminal can show and who answers each (contract x-dialogs). The daemon answers a
// routine one at launch, from what the founder already decided; every other dialog, and any not named here, is a person's: the agent
// shows needs_you and the person answers at its terminal. A test holds this table equal to the contract's.
package dialogs

// The classes of dialog.
const (
	Routine   = "routine"
	Human     = "human"
	NotRaised = "not raised"
	Unknown   = "unknown"
)

// The kinds of need a dialog that is a person's raises (needs_you.kind).
const (
	Permission = "permission"
	Question   = "question"
	Usage      = "usage"
	Dialog     = "dialog"
)

// Entry is one dialog: its class, and for a person's the kind it raises.
type Entry struct {
	Class string
	Kind  string
}

// Table is every dialog the contract names.
var Table = map[string]Entry{
	"folder_trust":       {Routine, ""},
	"bypass_warning":     {Routine, ""},
	"tool_permission":    {Human, Permission},
	"question":           {Human, Question},
	"usage_limit_choice": {Human, Usage},
	"exit_confirm":       {NotRaised, ""},
	"startup_notice":     {Unknown, Dialog},
	"any_other":          {Unknown, Dialog},
}

// Of is the entry for a dialog; a dialog the table does not name is any_other: unknown, which is a person's.
func Of(id string) Entry {
	if e, ok := Table[id]; ok {
		return e
	}
	return Table["any_other"]
}

// Answered says whether the daemon may answer the dialog itself, and then only at launch: it must be routine. Everything else is a
// person's.
func Answered(id string) bool { return Of(id).Class == Routine }
