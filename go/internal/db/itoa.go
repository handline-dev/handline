package db

import (
	"strconv"
	"strings"

	"github.com/handline/handline/go/internal/rpc"
)

func itoa(n int64) string { return strconv.FormatInt(n, 10) }

func jsonList(xs []string) string {
	parts := make([]string, len(xs))
	for i, x := range xs {
		parts[i] = rpc.PyJSON(x, false, true)
	}
	return "[" + strings.Join(parts, ", ") + "]"
}
