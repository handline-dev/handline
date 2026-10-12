package attachments

import (
	"database/sql"
	"encoding/base64"
	"fmt"

	"github.com/handline/handline/go/internal/rpc"
)

// Register adds attachment.put and attachment.get to the server's table.
func Register(srv *rpc.Server, s *Store) {
	srv.Register("attachment.put", rpc.Method{Role: "member", Write: true, Handler: s.put})
	srv.Register("attachment.get", rpc.Method{Role: "member", Handler: s.get})
}

func (s *Store) put(c *rpc.Conn, p map[string]any) (any, error) {
	in := PutIn{}
	name, ok := p["name"].(string)
	if !ok || FileName(name) == "" {
		return nil, badParam("name is the file's name", "name")
	}
	in.Name = name
	if in.Mime, ok = p["mime"].(string); !ok {
		return nil, badParam("mime is image/png, image/jpeg, image/gif or image/webp", "mime")
	}
	if in.Bytes, ok = rpc.AsInt(p["bytes"]); !ok {
		return nil, badParam("bytes is the size of the whole file", "bytes")
	}
	if v := p["offset"]; v != nil {
		if in.Offset, ok = rpc.AsInt(v); !ok || in.Offset < 0 {
			return nil, badParam("offset is where the chunk starts in the file", "offset")
		}
	}
	if v := p["upload"]; v != nil {
		if in.Upload, ok = v.(string); !ok {
			return nil, badParam("upload is an upload id", "upload")
		}
	}
	data, ok := p["data"].(string)
	if !ok {
		return nil, badParam("data is the chunk, in base64", "data")
	}
	var err error
	if in.Data, err = base64.StdEncoding.DecodeString(data); err != nil {
		return nil, badParam("data is not base64", "data")
	}
	var out *PutOut
	res, err := c.Effect(func(*sql.Tx) (map[string]any, error) {
		var err error
		if out, err = s.Put(in); err != nil {
			return nil, err
		}
		r := map[string]any{"accepted": true, "said": fmt.Sprintf("%d of %d bytes", out.Received, in.Bytes), "upload": out.Upload, "received": out.Received}
		if out.Attachment != nil {
			r["attachment"] = out.Attachment.Map()
		}
		return r, nil
	})
	return res, err
}

func (s *Store) get(_ *rpc.Conn, p map[string]any) (any, error) {
	id, ok := p["id"].(string)
	if !ok || !ValidID(id) {
		return nil, badParam("id is an attachment id", "id")
	}
	thumb := false
	if v := p["thumb"]; v != nil {
		if thumb, ok = v.(bool); !ok {
			return nil, badParam("thumb is true or false", "thumb")
		}
	}
	var offset int64
	if v := p["offset"]; v != nil {
		if offset, ok = rpc.AsInt(v); !ok || offset < 0 {
			return nil, badParam("offset is where the chunk starts in the file", "offset")
		}
	}
	g, err := s.Get(id, thumb, offset)
	if err != nil {
		return nil, err
	}
	return map[string]any{"mime": g.Mime, "bytes": g.Bytes, "offset": g.Start, "data": g.Data, "done": g.Done}, nil
}
