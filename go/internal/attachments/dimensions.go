package attachments

import (
	"encoding/binary"
	"image"
	"io"
	"os"
)

// headSize is how much of the file the size is read from: enough for a JPEG's Exif segment, which comes before its picture.
const headSize = 64 << 10

// dimensions is the picture's width and height as it is drawn, from its header; 0, 0 when it cannot be read. A JPEG whose Exif
// orientation turns it a quarter round (5 to 8) is drawn with the two swapped, as browsers and phones do.
func dimensions(path, mime string) (w, h int) {
	f, err := os.Open(path)
	if err != nil {
		return 0, 0
	}
	defer f.Close()
	head := make([]byte, headSize)
	n, err := io.ReadFull(f, head)
	if err != nil && err != io.ErrUnexpectedEOF {
		return 0, 0
	}
	head = head[:n]
	if mime == "image/webp" {
		return webpSize(head)
	}
	if _, err := f.Seek(0, io.SeekStart); err != nil {
		return 0, 0
	}
	cfg, _, err := image.DecodeConfig(f) // png, jpeg and gif are registered by get.go's imports
	if err != nil {
		return 0, 0
	}
	if mime == "image/jpeg" && jpegTurned(head) {
		return cfg.Height, cfg.Width
	}
	return cfg.Width, cfg.Height
}

// webpSize reads a WebP file's first 30 bytes: its lossy (VP8), lossless (VP8L) or extended (VP8X) header.
func webpSize(b []byte) (w, h int) {
	if len(b) < 30 || string(b[0:4]) != "RIFF" || string(b[8:12]) != "WEBP" {
		return 0, 0
	}
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

// jpegTurned is whether the JPEG's Exif orientation is 5 to 8, read from the segments before its picture data.
func jpegTurned(b []byte) bool {
	if len(b) < 4 || b[0] != 0xff || b[1] != 0xd8 {
		return false
	}
	for i := 2; i+4 <= len(b) && b[i] == 0xff; {
		marker, n := b[i+1], int(binary.BigEndian.Uint16(b[i+2:]))
		if marker == 0xda || n < 2 || i+2+n > len(b) { // the picture data starts: no Exif after it
			return false
		}
		if seg := b[i+4 : i+2+n]; marker == 0xe1 && len(seg) > 6 && string(seg[:6]) == "Exif\x00\x00" {
			o := exifOrientation(seg[6:])
			return o >= 5 && o <= 8
		}
		i += 2 + n
	}
	return false
}

// exifOrientation is the Orientation tag (0x0112) of the TIFF block's first IFD, 0 when there is none.
func exifOrientation(t []byte) int {
	if len(t) < 8 {
		return 0
	}
	var bo binary.ByteOrder
	switch string(t[:2]) {
	case "II":
		bo = binary.LittleEndian
	case "MM":
		bo = binary.BigEndian
	default:
		return 0
	}
	ifd := int(bo.Uint32(t[4:]))
	if ifd < 8 || ifd+2 > len(t) {
		return 0
	}
	for k, n := 0, int(bo.Uint16(t[ifd:])); k < n; k++ {
		e := ifd + 2 + 12*k
		if e+12 > len(t) {
			return 0
		}
		if bo.Uint16(t[e:]) == 0x0112 {
			return int(bo.Uint16(t[e+8:]))
		}
	}
	return 0
}
