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
	"fmt"
	"math/big"
	"net"
	"net/http"
	"time"

	"github.com/gorilla/websocket"

	"github.com/handline/handline/go/internal/rpc"
	"github.com/handline/handline/go/internal/store"
	"github.com/handline/handline/go/internal/version"
)

// networkListener is the skeleton of the network door: the same JSON-RPC over an encrypted WebSocket, every
// caller anonymous until devices are paired (a later task), so hello is all it answers.
type networkListener struct {
	ln          net.Listener
	srv         *http.Server
	Fingerprint string // of the certificate, for the pairing QR
}

func listenNetwork(cfg Config, d *Daemon) (*networkListener, error) {
	var cert tls.Certificate
	var err error
	if cfg.CertFile != "" || cfg.KeyFile != "" {
		cert, err = tls.LoadX509KeyPair(cfg.CertFile, cfg.KeyFile)
	} else {
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
	rs := rpc.NewServer(cfg.Auth, version.Sha, store.LatestVersion(), "network", cfg.Log)
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
		rs.Serve(rpc.NewWSTransport(ws), 0)
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

func ephemeralCert() (tls.Certificate, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return tls.Certificate{}, err
	}
	tmpl := &x509.Certificate{
		SerialNumber: big.NewInt(time.Now().UnixNano()),
		Subject:      pkix.Name{CommonName: "handline daemon"},
		NotBefore:    time.Now().Add(-time.Minute),
		NotAfter:     time.Now().Add(365 * 24 * time.Hour),
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
