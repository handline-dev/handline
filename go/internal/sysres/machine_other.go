//go:build !darwin && !linux

package sysres

import "errors"

func thisMachine() (Machine, error) {
	return Machine{}, errors.New("RAM and load are read on macOS and Linux only")
}
