package daemon

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/pem"
	"fmt"
	"math/big"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"time"

	"github.com/gorilla/websocket"

	"github.com/handline/handline/go/internal/rpc"
)

// networkListener is the network door: the same JSON-RPC over an encrypted WebSocket, closed to every caller a
// device token has not opened (see rpc.NetGate).
type networkListener struct {
	ln          net.Listener
	srv         *http.Server
	Fingerprint string // of the certificate, for the pairing QR
	rs          *rpc.Server
}

func listenNetwork(cfg Config, d *Daemon) (*networkListener, error) {
	if err := checkBind(cfg.Listen); err != nil {
		return nil, err
	}
	var cert tls.Certificate
	var err error
	switch {
	case cfg.CertFile != "" || cfg.KeyFile != "":
		cert, err = tls.LoadX509KeyPair(cfg.CertFile, cfg.KeyFile)
	case cfg.Home != "":
		cert, err = homeCert(cfg.Home)
	default:
		cert, err = ephemeralCert()
	}
	if err != nil {
		return nil, fmt.Errorf("network listener certificate: %w", err)
	}
	sum := sha256.Sum256(cert.Certificate[0])
	nl := &networkListener{Fingerprint: hex.EncodeToString(sum[:])}
	ln, err := tls.Listen("tcp", cfg.Listen, &tls.Config{Certificates: []tls.Certificate{cert}, MinVersion: tls.VersionTLS13})
	if err != nil {
		return nil, err
	}
	nl.ln = ln
	d.dev.Listening(ln.Addr().String(), nl.Fingerprint)
	rs := d.newServer("network")
	nl.rs = rs
	up := websocket.Upgrader{CheckOrigin: func(*http.Request) bool { return true }} // callers are not browsers' pages; the token decides
	mux := http.NewServeMux()
	mux.HandleFunc("/rpc", func(w http.ResponseWriter, r *http.Request) {
		ws, err := up.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		if !d.trackWS(ws.UnderlyingConn()) {
			ws.Close()
			return
		}
		defer d.untrack(ws.UnderlyingConn())
		host, _, _ := net.SplitHostPort(r.RemoteAddr)
		rs.ServeNet(rpc.NewWSTransport(ws), host, func() { ws.Close() })
	})
	nl.srv = &http.Server{Handler: mux, ReadHeaderTimeout: rpc.RecvTimeout}
	go nl.srv.Serve(ln)
	cfg.Log("network listener on %s, certificate %s", ln.Addr(), nl.Fingerprint)
	return nl, nil
}

func (d *Daemon) trackWS(c net.Conn) bool { return d.track(c) }

func (n *networkListener) close() { n.srv.Close() }

// Addr is where the listener is bound.
func (d *Daemon) NetworkAddr() string {
	if d.net == nil {
		return ""
	}
	return d.net.ln.Addr().String()
}

func (d *Daemon) NetworkFingerprint() string {
	if d.net == nil {
		return ""
	}
	return d.net.Fingerprint
}

// checkBind refuses an address the listener must never take: only an IP literal of this machine's own LAN,
// Tailscale or loopback (never a wildcard, and never a public address), because a phone's door is not the internet's.
func checkBind(addr string) error {
	host, _, err := net.SplitHostPort(addr)
	if err != nil {
		return fmt.Errorf("network listener address %q: %w", addr, err)
	}
	ip := net.ParseIP(host)
	switch {
	case ip == nil:
		return fmt.Errorf("network listener address %q: the host must be an IP address of this machine's LAN or Tailscale", addr)
	case ip.IsUnspecified():
		return fmt.Errorf("network listener address %q binds every interface; name the LAN or Tailscale address", addr)
	case ip.IsLoopback(), ip.IsPrivate(), ip.IsLinkLocalUnicast(), tailscaleV4.Contains(ip):
		return nil
	}
	return fmt.Errorf("network listener address %q is a public address; only loopback, LAN and Tailscale are served", addr)
}

var _, tailscaleV4, _ = net.ParseCIDR("100.64.0.0/10")

// homeCert is the listener's certificate kept in the home, made once: a paired device pinned its fingerprint,
// so it must survive a restart.
func homeCert(home string) (tls.Certificate, error) {
	crt, key := filepath.Join(home, "network.crt"), filepath.Join(home, "network.key")
	if cert, err := tls.LoadX509KeyPair(crt, key); err == nil {
		return cert, nil
	} else if !os.IsNotExist(err) {
		return tls.Certificate{}, err
	}
	cert, err := ephemeralCert()
	if err != nil {
		return cert, err
	}
	der, err := x509.MarshalECPrivateKey(cert.PrivateKey.(*ecdsa.PrivateKey))
	if err != nil {
		return cert, err
	}
	if err := os.WriteFile(key, pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: der}), 0o600); err != nil {
		return cert, err
	}
	return cert, os.WriteFile(crt, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: cert.Certificate[0]}), 0o644)
}

func ephemeralCert() (tls.Certificate, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return tls.Certificate{}, err
	}
	tmpl := &x509.Certificate{
		SerialNumber: big.NewInt(time.Now().UnixNano()),
		Subject:      pkix.Name{CommonName: "handline daemon"},
		NotBefore:    time.Now().Add(-time.Minute),
		NotAfter:     time.Now().Add(10 * 365 * 24 * time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		IPAddresses:  []net.IP{net.ParseIP("127.0.0.1"), net.ParseIP("::1")},
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &key.PublicKey, key)
	if err != nil {
		return tls.Certificate{}, err
	}
	return tls.Certificate{Certificate: [][]byte{der}, PrivateKey: key}, nil
}
