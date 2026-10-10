// Package pyval is Python's reading of a decoded JSON value, for the legacy verbs, whose requests the Python daemon read with str(), bool()
// and int() and whose replies must stay the same.
package pyval

import (
	"encoding/json"
	"strconv"
	"strings"
)

// Str is Python's str() of a value of a decoded request.
func Str(v any) string {
	switch x := v.(type) {
	case nil:
		return "None"
	case string:
		return x
	case bool:
		if x {
			return "True"
		}
		return "False"
	case json.Number:
		return x.String()
	}
	b, _ := json.Marshal(v)
	return string(b)
}

// Truthy is Python's bool() of a decoded value.
func Truthy(v any) bool {
	switch x := v.(type) {
	case nil:
		return false
	case bool:
		return x
	case string:
		return x != ""
	case json.Number:
		f, err := strconv.ParseFloat(x.String(), 64)
		return err != nil || f != 0
	case []any:
		return len(x) > 0
	case map[string]any:
		return len(x) > 0
	}
	return true
}

// Int is Python's int() of a decoded value: a whole number, a boolean, or text that is one. ok is false where Python raises.
func Int(v any) (n int64, ok bool) {
	switch x := v.(type) {
	case bool:
		if x {
			return 1, true
		}
		return 0, true
	case json.Number:
		if n, err := x.Int64(); err == nil {
			return n, true
		}
		f, err := x.Float64()
		return int64(f), err == nil
	case string:
		n, err := strconv.ParseInt(strings.TrimSpace(x), 10, 64)
		return n, err == nil
	}
	return 0, false
}
