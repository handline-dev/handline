package daemon

import (
	"errors"
	"net"
	"strings"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/hlclient"
)

// A request past the line limit is answered with a size error the caller can read, not a hang-up it cannot tell from a crash.
func TestARequestPastTheLineLimitIsABadRequestTheClientSees(t *testing.T) {
	d := start(t, fakeAuth{agent: "alice"}, "")
	c := &hlclient.Client{Home: d.cfg.Home, Deadline: 10 * time.Second,
		Dial: func() (net.Conn, error) { return net.Dial("unix", d.cfg.SocketFile) }}
	_, err := c.Call("whoami", map[string]any{"text": strings.Repeat("x", 2<<20)})
	var refused *hlclient.RPCError
	if !errors.As(err, &refused) || refused.Code != "bad_request" || !strings.Contains(refused.Message, "at most 1048576 bytes") {
		t.Fatalf("%v", err)
	}
}
