package housekeeping

import (
	"fmt"
	"os"
)

// ScratchRoot is the Claude sessions' shared scratch root, /private/tmp/claude-<uid>.
func ScratchRoot() string { return fmt.Sprintf("/private/tmp/claude-%d", os.Getuid()) }
