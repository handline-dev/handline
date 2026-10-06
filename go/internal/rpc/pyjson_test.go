package rpc

import (
	"encoding/json"
	"testing"
)

func TestPyJSONWritesWhatPythonsDumpsWrites(t *testing.T) {
	v := map[string]any{"b": []any{json.Number("1"), true, nil}, "a": "é\n\"\x7f😀", "c": map[string]any{}, "d": []string{"x"}}
	if got, want := PyJSON(v, true, false), "{\"a\": \"é\\n\\\"\x7f😀\", \"b\": [1, true, null], \"c\": {}, \"d\": [\"x\"]}"; got != want {
		t.Errorf("%s\nwant %s", got, want)
	}
	if got, want := PyJSON("\u00e9\x7f\U0001F600<", false, true), "\"\\u00e9\\u007f\\ud83d\\ude00<\""; got != want {
		t.Errorf("%s want %s", got, want)
	}
}
