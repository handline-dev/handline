package daemon

import (
	"bufio"
	"encoding/json"
	"net"
	"reflect"
	"sort"
	"testing"

	"github.com/handline/handline/go/internal/rpc"
)

// A port adds its capability and its registrar on its own line; hello still reports the same capabilities, and its methods by name, whatever
// order the packages registered in.
func TestHelloReportsTheDeclaredCapabilitiesAndTheMethodsByName(t *testing.T) {
	d, err := startAt(t, t.TempDir(), false)
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	srvEnd, cli := net.Pipe()
	go d.local.Serve(rpc.NewLineTransport(srvEnd), 0)
	defer cli.Close()
	cli.Write([]byte(`{"jsonrpc":"2.0","id":1,"method":"hello","params":{"client":"t","protocol":1}}` + "\n"))
	line, err := bufio.NewReader(cli).ReadBytes('\n')
	if err != nil {
		t.Fatal(err)
	}
	var r struct {
		Result struct {
			Capabilities map[string]any
			Methods      []struct{ Name string }
		}
	}
	json.Unmarshal(line, &r)
	want := map[string]any{"chat": false, "review": false, "terminal_format": []any{"text", "bytes", "styled"},
		"terminal_hosts": map[string]any{"tmux": []any{"text", "bytes", "styled"}}, "transcript": false}
	if !reflect.DeepEqual(r.Result.Capabilities, want) {
		t.Fatalf("%v", r.Result.Capabilities)
	}
	var names []string
	for _, m := range r.Result.Methods {
		names = append(names, m.Name)
	}
	if !sort.StringsAreSorted(names) || len(names) < 60 {
		t.Fatalf("%d methods, sorted %v", len(names), sort.StringsAreSorted(names))
	}
}
