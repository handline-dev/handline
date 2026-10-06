package rpc

import (
	"bytes"
	"io"
	"net"
	"time"

	"github.com/gorilla/websocket"
)

// LineTransport is the local socket's framing: one JSON object per line.
type LineTransport struct {
	conn net.Conn
	buf  []byte
}

func NewLineTransport(c net.Conn) *LineTransport { return &LineTransport{conn: c} }

func (t *LineTransport) Pending() bool { return len(t.buf) > 0 }

// ReadMessage returns the next line, or what is left at EOF (a client that shuts its write side after one
// object needs no newline); io.EOF at a clean end. A line past MaxLineBytes is a bad_request.
func (t *LineTransport) ReadMessage(timeout time.Duration) ([]byte, error) {
	chunk := make([]byte, 65536)
	for {
		if i := bytes.IndexByte(t.buf, '\n'); i >= 0 {
			line := t.buf[:i:i]
			t.buf = t.buf[i+1:]
			return line, nil
		}
		if len(t.buf) > MaxLineBytes {
			return nil, BadRequestError("a line is at most 1048576 bytes", "", BadRequest)
		}
		t.conn.SetReadDeadline(time.Now().Add(timeout))
		n, err := t.conn.Read(chunk)
		t.buf = append(t.buf, chunk[:n]...)
		if err != nil {
			if err == io.EOF && len(t.buf) > 0 {
				line := t.buf
				t.buf = nil
				return line, nil
			}
			return nil, err
		}
		timeout = RecvTimeout // a line that has started must finish
	}
}

func (t *LineTransport) WriteMessage(b []byte) error {
	t.conn.SetWriteDeadline(time.Now().Add(RecvTimeout))
	_, err := t.conn.Write(append(b, '\n'))
	return err
}

// WSTransport is the network listener's framing: one JSON object per text frame.
type WSTransport struct {
	ws *websocket.Conn
}

func NewWSTransport(ws *websocket.Conn) *WSTransport {
	ws.SetReadLimit(MaxLineBytes)
	return &WSTransport{ws: ws}
}

func (t *WSTransport) Pending() bool { return false }

func (t *WSTransport) ReadMessage(timeout time.Duration) ([]byte, error) {
	t.ws.SetReadDeadline(time.Now().Add(timeout))
	typ, b, err := t.ws.ReadMessage()
	if err != nil {
		if err == websocket.ErrReadLimit {
			return nil, BadRequestError("a message is at most 1048576 bytes", "", BadRequest)
		}
		return nil, err
	}
	if typ != websocket.TextMessage {
		return nil, BadRequestError("messages are text frames", "", BadRequest)
	}
	return b, nil
}

func (t *WSTransport) WriteMessage(b []byte) error {
	t.ws.SetWriteDeadline(time.Now().Add(RecvTimeout))
	return t.ws.WriteMessage(websocket.TextMessage, b)
}
