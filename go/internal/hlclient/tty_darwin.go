package hlclient

import "golang.org/x/sys/unix"

// StdinIsTerminal is sys.stdin.isatty(): a terminal, not merely a character device such as /dev/null.
func StdinIsTerminal() bool {
	_, err := unix.IoctlGetTermios(0, unix.TIOCGETA)
	return err == nil
}
