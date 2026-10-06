// Package rpc is the daemon's JSON-RPC 2.0 connection (contract/protocol.md, "Framing", "Handshake" and
// "Errors"): one object per message, many calls on one connection, hello first, every failure a typed error.
package rpc

import "fmt"

const (
	BadRequest    = -32600
	NotFound      = -32001
	RefusedCode   = -32002
	Internal      = -32603
	ParseError    = -32700
	InvalidParams = -32602
	NoSuchMethod  = -32601
)

// Error is a call that failed as the wire says it: the JSON-RPC code, the stable data.code, the rest of data.
type Error struct {
	RPCCode int
	Code    string
	Message string
	Data    map[string]any
}

func (e *Error) Error() string { return fmt.Sprintf("%s: %s", e.Code, e.Message) }

func (e *Error) wire() map[string]any {
	data := map[string]any{"code": e.Code}
	for k, v := range e.Data {
		data[k] = v
	}
	return map[string]any{"code": e.RPCCode, "message": e.Message, "data": data}
}

// BadRequestError is a malformed call; field names the offending member when there is one.
func BadRequestError(message, field string, rpcCode int) *Error {
	e := &Error{RPCCode: rpcCode, Code: "bad_request", Message: message}
	if field != "" {
		e.Data = map[string]any{"field": field}
	}
	return e
}

// NotFoundError says what was not found.
func NotFoundError(message string) *Error {
	return &Error{RPCCode: NotFound, Code: "not_found", Message: message}
}

// Refused is fail-closed: a refusal carries the reason, the gate that said no and a hint when one helps.
func Refused(reason, gate, hint string) *Error {
	d := map[string]any{"reason": reason}
	if gate != "" {
		d["gate"] = gate
	}
	if hint != "" {
		d["hint"] = hint
	}
	return &Error{RPCCode: RefusedCode, Code: "refused", Message: reason, Data: d}
}
