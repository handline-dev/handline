package attachments

import (
	"bytes"
	"encoding/base64"
	"image"
	"image/color"
	"image/draw"
	"image/gif"
	"image/jpeg"
	"image/png"
	"io"
	"os"
)

// ThumbSide is the longest side of the small copy a client draws.
const ThumbSide = 320

// maxPixels is the largest picture the daemon decodes to make a small copy; a larger one is served whole.
const maxPixels = 64 << 20

// GetOut is attachment.get's reply.
type GetOut struct {
	Mime         string
	Bytes, Start int64
	Data         string
	Done         bool
}

// Get reads the image, or its small copy, from offset: one chunk.
func (s *Store) Get(id string, thumb bool, offset int64) (*GetOut, error) {
	info, err := s.Lookup(id)
	if err != nil {
		return nil, err
	}
	path, mime := s.Path(info), info.Mime
	if thumb {
		if p, m, ok := s.thumbOf(info); ok {
			path, mime = p, m
		}
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, unknownImage(id)
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return nil, err
	}
	if offset < 0 || offset > st.Size() {
		return nil, badParam("offset is within the file", "offset")
	}
	buf := make([]byte, min(int64(MaxChunk), st.Size()-offset))
	if _, err := f.ReadAt(buf, offset); err != nil && err != io.EOF {
		return nil, err
	}
	return &GetOut{Mime: mime, Bytes: st.Size(), Start: offset, Data: base64.StdEncoding.EncodeToString(buf), Done: offset+int64(len(buf)) >= st.Size()}, nil
}

// thumbOf is the small copy of the image, made on first use and kept until the image goes. A type the daemon cannot decode (WebP), or
// a picture too large to decode, has none: the caller serves the image itself.
func (s *Store) thumbOf(info Info) (path, mime string, ok bool) {
	mime = "image/png"
	if info.Mime == "image/jpeg" {
		mime = "image/jpeg"
	}
	path = s.sub("thumbs", info.ID+".thumb")
	if _, err := os.Stat(path); err == nil {
		return path, mime, true
	}
	b, err := os.ReadFile(s.Path(info))
	if err != nil {
		return "", "", false
	}
	small, err := shrink(b, info.Mime)
	if err != nil {
		return "", "", false
	}
	if os.WriteFile(path+".tmp", small, 0o600) != nil || os.Rename(path+".tmp", path) != nil {
		return "", "", false
	}
	return path, mime, true
}

func shrink(b []byte, mime string) ([]byte, error) {
	cfg, _, err := image.DecodeConfig(bytes.NewReader(b))
	if err != nil {
		return nil, err
	}
	if cfg.Width <= 0 || cfg.Height <= 0 || cfg.Width*cfg.Height > maxPixels {
		return nil, image.ErrFormat
	}
	var src image.Image
	switch mime {
	case "image/png":
		src, err = png.Decode(bytes.NewReader(b))
	case "image/jpeg":
		src, err = jpeg.Decode(bytes.NewReader(b))
	case "image/gif":
		src, err = gif.Decode(bytes.NewReader(b)) // the first frame
	default:
		return nil, image.ErrFormat
	}
	if err != nil {
		return nil, err
	}
	small := scale(src, ThumbSide)
	var out bytes.Buffer
	if mime == "image/jpeg" {
		err = jpeg.Encode(&out, small, &jpeg.Options{Quality: 80})
	} else {
		err = png.Encode(&out, small)
	}
	return out.Bytes(), err
}

// scale is src with its longest side at most side, each new pixel the mean of the block it covers.
func scale(src image.Image, side int) *image.NRGBA {
	b := src.Bounds()
	w, h := b.Dx(), b.Dy()
	nw, nh := w, h
	if w >= h && w > side {
		nw, nh = side, max(1, h*side/w)
	} else if h > w && h > side {
		nw, nh = max(1, w*side/h), side
	}
	rgba := image.NewNRGBA(image.Rect(0, 0, w, h))
	draw.Draw(rgba, rgba.Bounds(), src, b.Min, draw.Src)
	if nw == w && nh == h {
		return rgba
	}
	dst := image.NewNRGBA(image.Rect(0, 0, nw, nh))
	for y := 0; y < nh; y++ {
		y0, y1 := y*h/nh, max(y*h/nh+1, (y+1)*h/nh)
		for x := 0; x < nw; x++ {
			x0, x1 := x*w/nw, max(x*w/nw+1, (x+1)*w/nw)
			var r, g, bl, a, n uint64
			for yy := y0; yy < y1; yy++ {
				for xx := x0; xx < x1; xx++ {
					c := rgba.NRGBAAt(xx, yy)
					al := uint64(c.A)
					r += uint64(c.R) * al
					g += uint64(c.G) * al
					bl += uint64(c.B) * al
					a += al
					n++
				}
			}
			if a == 0 {
				dst.SetNRGBA(x, y, color.NRGBA{})
				continue
			}
			dst.SetNRGBA(x, y, color.NRGBA{uint8(r / a), uint8(g / a), uint8(bl / a), uint8(a / n)})
		}
	}
	return dst
}
