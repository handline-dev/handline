// Package delivery is how a message reaches an agent and is recorded: the delivery mode, the states of a send, the receipt a retry
// gets back, and the ledger that keeps them across a restart. Terminal agents' peer sends and the CLI client (package claudecli) share it.
package delivery

import (
	"crypto/sha256"
	"fmt"
)

// Delivery says what a message does when a turn is running: Now steers it into the running turn (priority "now"); Queue holds it
// until the turn ends.
type Delivery string

const (
	Now   Delivery = "now"
	Queue Delivery = "queue"
)

// State of a send: Queued (held behind a turn), Sent (written to the CLI), Confirmed (the CLI echoed it back by uuid, or reported it queued or started).
type State string

const (
	Queued    State = "queued"
	Sent      State = "sent"
	Confirmed State = "confirmed"
	// Unknown: the message was written, and the daemon or the CLI died before the echo. The CLI may or may not have run it, so it is
	// never written again; the sender is told.
	Unknown State = "unknown"
	// Withdrawn: the message was held and its sender took it back before any write began. It is never written.
	Withdrawn State = "withdrawn"
)

// Receipt is the recorded answer of one operation; a retry with the same operation id gets it back and writes nothing.
type Receipt struct {
	Op    string
	UUID  string
	State State
}

// UUIDFor is a valid UUID that is the same for the same operation id, so the CLI's echo names the operation.
func UUIDFor(op string) string {
	h := sha256.Sum256([]byte("handline-operation:" + op))
	b := h[:16]
	b[6] = b[6]&0x0f | 0x50
	b[8] = b[8]&0x3f | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}
