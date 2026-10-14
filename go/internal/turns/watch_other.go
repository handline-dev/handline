//go:build !darwin && !linux

package turns

import "errors"

func watchFile(string, func(), func()) (func(), error) {
	return nil, errors.New("no file watch on this platform")
}
