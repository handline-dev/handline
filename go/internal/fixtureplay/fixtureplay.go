// Package fixtureplay reads contract/fixtures files and matches replies against them, for the Go tests that play fixtures the daemon
// binary's conformance run cannot (they need agents or hosts it has not got). contract/conformance.py is the full harness; this is its
// matching and its {{name}} filling, nothing else.
package fixtureplay

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strings"
	"time"
)

// Fixture is one scripted exchange: `as` is its own connection's role, `with` the extra connections by name and role.
type Fixture struct {
	Name  string
	As    string
	With  map[string]string
	Steps []map[string]any
}

// Load reads a fixtures file.
func Load(path string) ([]Fixture, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var file struct{ Fixtures []Fixture }
	if err := json.Unmarshal(raw, &file); err != nil {
		return nil, err
	}
	return file.Fixtures, nil
}

// Match is conformance.py's matching: every key of an object in want matches, arrays element by element, else equal; the matchers
// $type, $oneOf, $contains, $regex, $save and $absent (as a key's value).
func Match(want, got any, saved map[string]any, at string) error {
	w, ok := want.(map[string]any)
	if !ok {
		if fmt.Sprint(want) != fmt.Sprint(got) {
			return fmt.Errorf("%s: wanted %v, got %v", at, want, got)
		}
		return nil
	}
	special := false
	for k := range w {
		special = special || strings.HasPrefix(k, "$")
	}
	if !special {
		g, ok := got.(map[string]any)
		if !ok {
			return fmt.Errorf("%s: wanted an object, got %v", at, got)
		}
		for k, v := range w {
			if m, ok := v.(map[string]any); ok && m["$absent"] == true {
				if _, has := g[k]; has {
					return fmt.Errorf("%s.%s: wanted absent", at, k)
				}
				continue
			}
			if _, has := g[k]; !has {
				return fmt.Errorf("%s: no %s in %v", at, k, g)
			}
			if err := Match(v, g[k], saved, at+"."+k); err != nil {
				return err
			}
		}
		return nil
	}
	for op, arg := range w {
		switch op {
		case "$type":
			if !isType(arg.(string), got) {
				return fmt.Errorf("%s: wanted %v, got %v", at, arg, got)
			}
		case "$oneOf":
			ok := false
			for _, alt := range arg.([]any) {
				ok = ok || Match(alt, got, saved, at) == nil
			}
			if !ok {
				return fmt.Errorf("%s: wanted one of %v, got %v", at, arg, got)
			}
		case "$contains":
			list, _ := got.([]any)
			ok := false
			for _, item := range list {
				ok = ok || Match(arg, item, saved, at) == nil
			}
			if !ok {
				return fmt.Errorf("%s: no element matches %v in %v", at, arg, got)
			}
		case "$regex":
			if s, ok := got.(string); !ok || !regexp.MustCompile(arg.(string)).MatchString(s) {
				return fmt.Errorf("%s: wanted /%v/, got %v", at, arg, got)
			}
		case "$save":
			saved[arg.(string)] = got
		default:
			return fmt.Errorf("%s: matcher %s", at, op)
		}
	}
	return nil
}

func isType(kind string, got any) bool {
	switch kind {
	case "null":
		return got == nil
	case "string":
		_, ok := got.(string)
		return ok
	case "timestamp":
		s, ok := got.(string)
		_, err := time.Parse(time.RFC3339, s)
		return ok && err == nil
	case "integer":
		f, ok := got.(float64)
		return ok && f == float64(int64(f))
	case "number":
		_, ok := got.(float64)
		return ok
	case "boolean":
		_, ok := got.(bool)
		return ok
	case "object":
		_, ok := got.(map[string]any)
		return ok
	case "array":
		_, ok := got.([]any)
		return ok
	}
	return false
}

// Fill replaces each string that is a whole "{{name}}" with vars[name], keeping its type.
func Fill(v any, vars map[string]any) any {
	switch x := v.(type) {
	case string:
		if strings.HasPrefix(x, "{{") && strings.HasSuffix(x, "}}") {
			if val, ok := vars[x[2:len(x)-2]]; ok {
				return val
			}
		}
		return x
	case map[string]any:
		out := map[string]any{}
		for k, e := range x {
			out[k] = Fill(e, vars)
		}
		return out
	case []any:
		out := make([]any, len(x))
		for i, e := range x {
			out[i] = Fill(e, vars)
		}
		return out
	}
	return v
}
