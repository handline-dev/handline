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
	"errors"
	"fmt"
	"math/big"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/gorilla/websocket"

	"github.com/handline/handline/go/internal/rpc"
)

// networkListener is the network door: the same JSON-RPC over an encrypted WebSocket, closed to every caller a
// device token has not opened (see rpc.NetGate).
type networkListener struct {
	ln  net.Listener
	srv *http.Server
	rs  *rpc.Server
	src *certSource
}

// Fingerprint is the sha256 of the certificate the listener presents now, for the pairing QR.
func (n *networkListener) Fingerprint() string {
	c, err := n.src.get()
	if err != nil || len(c.Certificate) == 0 {
		return ""
	}
	sum := sha256.Sum256(c.Certificate[0])
	return hex.EncodeToString(sum[:])
}

// certSource is the certificate a listener presents. Made in memory or kept in the home it never changes; read from
// the files of a gateway it is read again when either file changes, so a renewed certificate (tailscale cert) is
// served without a restart.
type certSource struct {
	certFile, keyFile string
	mu                sync.Mutex
	cert              *tls.Certificate
	stamp             [2]time.Time
}

func (s *certSource) get() (*tls.Certificate, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.certFile == "" {
		return s.cert, nil
	}
	var stamp [2]time.Time
	for i, f := range []string{s.certFile, s.keyFile} {
		st, err := os.Stat(f)
		if err != nil {
			return s.cert, err
		}
		stamp[i] = st.ModTime()
	}
	if s.cert != nil && stamp == s.stamp {
		return s.cert, nil
	}
	c, err := tls.LoadX509KeyPair(s.certFile, s.keyFile)
	if err != nil {
		if s.cert != nil {
			return s.cert, nil // a half-written renewal: keep serving the last good one
		}
		return nil, err
	}
	s.cert, s.stamp = &c, stamp
	return s.cert, nil
}

func listenNetwork(cfg Config, d *Daemon) (*networkListener, error) {
	if err := checkBind(cfg.Listen); err != nil {
		return nil, err
	}
	src := &certSource{}
	switch {
	case cfg.CertFile != "" || cfg.KeyFile != "":
		src.certFile, src.keyFile = cfg.CertFile, cfg.KeyFile
		if _, err := src.get(); err != nil {
			return nil, fmt.Errorf("network listener certificate: %w", err)
		}
	case cfg.Gateway:
		return nil, errors.New("a gateway needs -cert and -key: it makes no certificate of its own")
	default:
		cert, err := networkCert(cfg.Home)
		if err != nil {
			return nil, fmt.Errorf("network listener certificate: %w", err)
		}
		src.cert = &cert
	}
	nl := &networkListener{src: src}
	ln, err := tls.Listen("tcp", cfg.Listen, &tls.Config{GetCertificate: func(*tls.ClientHelloInfo) (*tls.Certificate, error) { return src.get() },
		MinVersion: tls.VersionTLS13})
	if err != nil {
		return nil, err
	}
	nl.ln = ln
	advertise := cfg.Advertise
	if advertise == "" {
		advertise = ln.Addr().String()
	}
	d.dev.Listening(advertise, nl.Fingerprint)
	rs := d.newServer("network")
	nl.rs = rs
	up := websocket.Upgrader{CheckOrigin: sameOrigin} // a browser page may only be this listener's own; the token decides the rest
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
		host := remoteHost(r.RemoteAddr)
		rs.ServeNet(rpc.NewWSTransport(ws), host, func() { ws.Close() })
	})
	if cfg.WebDir != "" {
		ui, err := webUI(cfg.WebDir)
		if err != nil {
			ln.Close()
			return nil, fmt.Errorf("web UI directory: %w", err)
		}
		mux.Handle("/", ui)
	}
	nl.srv = &http.Server{Handler: mux, ReadHeaderTimeout: rpc.RecvTimeout}
	go nl.srv.Serve(ln)
	cfg.Log("network listener on %s, certificate %s", ln.Addr(), nl.Fingerprint())
	return nl, nil
}

// sameOrigin lets a call without an Origin through (a native client) and a browser's only from the page this
// listener served.
func sameOrigin(r *http.Request) bool {
	o := r.Header.Get("Origin")
	if o == "" {
		return true
	}
	u, err := url.Parse(o)
	return err == nil && u.Host == r.Host
}

func remoteHost(addr string) string {
	host, _, err := net.SplitHostPort(addr)
	if err != nil {
		return addr
	}
	return host
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
	return d.net.Fingerprint()
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
func networkCert(home string) (tls.Certificate, error) {
	if home == "" {
		return ephemeralCert()
	}
	return homeCert(home)
}

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
