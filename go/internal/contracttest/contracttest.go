// Package contracttest is for tests: the contract is one file per method group (contract/spec/), and Spec joins them as
// contract/generate.py does, so a test reads the whole protocol as one JSON document.
package contracttest

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

// Spec is contract/spec/*.json joined: the methods one list, the components one map per kind, every other key as it is.
func Spec(t testing.TB) []byte {
	t.Helper()
	_, here, _, _ := runtime.Caller(0)
	files, err := filepath.Glob(filepath.Join(filepath.Dir(here), "..", "..", "..", "contract", "spec", "*.json"))
	if err != nil || len(files) == 0 {
		t.Fatalf("contract/spec: %v, %d files", err, len(files))
	}
	spec := map[string]any{}
	var methods []json.RawMessage
	components := map[string]map[string]json.RawMessage{}
	for _, f := range files {
		raw, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		var part struct {
			Methods    []json.RawMessage                     `json:"methods"`
			Components map[string]map[string]json.RawMessage `json:"components"`
		}
		var rest map[string]json.RawMessage
		if err := json.Unmarshal(raw, &part); err != nil {
			t.Fatalf("%s: %v", f, err)
		}
		if err := json.Unmarshal(raw, &rest); err != nil {
			t.Fatalf("%s: %v", f, err)
		}
		methods = append(methods, part.Methods...)
		for kind, items := range part.Components {
			if components[kind] == nil {
				components[kind] = map[string]json.RawMessage{}
			}
			for name, v := range items {
				components[kind][name] = v
			}
		}
		for k, v := range rest {
			if k != "methods" && k != "components" {
				spec[k] = v
			}
		}
	}
	spec["methods"], spec["components"] = methods, components
	out, err := json.Marshal(spec)
	if err != nil {
		t.Fatal(err)
	}
	return out
}
