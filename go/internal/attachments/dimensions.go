package attachments

import (
	"encoding/binary"
	"image"
	"os"
)

// dimensions is the image's width and height from its header, 0, 0 when it cannot be read: a client sizes the picture before it arrives.
func dimensions(path, mime string) (w, h int) {
	f, err := os.Open(path)
	if err != nil {
		return 0, 0
	}
	defer f.Close()
	if mime == "image/webp" {
		head := make([]byte, 30)
		if n, _ := f.Read(head); n == len(head) {
			return webpSize(head)
		}
		return 0, 0
	}
	cfg, _, err := image.DecodeConfig(f) // png, jpeg and gif are registered by get.go's imports
	if err != nil {
		return 0, 0
	}
	return cfg.Width, cfg.Height
}

// webpSize reads the first 30 bytes of a WebP file: its lossy (VP8), lossless (VP8L) or extended (VP8X) header.
func webpSize(b []byte) (w, h int) {
	switch string(b[12:16]) {
	case "VP8 ":
		if b[23] == 0x9d && b[24] == 0x01 && b[25] == 0x2a {
			return int(binary.LittleEndian.Uint16(b[26:28]) & 0x3fff), int(binary.LittleEndian.Uint16(b[28:30]) & 0x3fff)
		}
	case "VP8L":
		if b[20] == 0x2f {
			bits := binary.LittleEndian.Uint32(b[21:25])
			return int(bits&0x3fff) + 1, int(bits>>14&0x3fff) + 1
		}
	case "VP8X":
		return int(uint32(b[24])|uint32(b[25])<<8|uint32(b[26])<<16) + 1, int(uint32(b[27])|uint32(b[28])<<8|uint32(b[29])<<16) + 1
	}
	return 0, 0
}
