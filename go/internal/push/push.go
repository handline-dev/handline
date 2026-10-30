// Package push sends a phone notification to each paired device that gave a push token (device.push_token): straight to Apple's APNs
// and Google's FCM from the founder's machine, outbound only, with no relay. The credentials are files config.yaml names; with none
// named, push is off.
package push

import (
	"context"
	"crypto"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"time"

	"github.com/handline/handline/go/internal/devices"
	"github.com/handline/handline/go/internal/usage"
)

// The config.yaml keys. APNs needs the .p8 key with its key id and team id; FCM needs the service account JSON.
const (
	KeyAPNsKey    = "push_apns_key"
	KeyAPNsKeyID  = "push_apns_key_id"
	KeyAPNsTeamID = "push_apns_team_id"
	KeyAPNsTopic  = "push_apns_topic" // the app's bundle id
	KeyFCM        = "push_fcm_credentials"

	defaultTopic = "dev.handline.mobile"
	sendTimeout  = 15 * time.Second
)

// Note is one notification. Silent is a badge update alone: nothing is shown.
type Note struct {
	Title, Body string
	Data        map[string]string // what a tap opens
	Collapse    string            // a later note with the same id takes this one's place
	Badge       int
	Silent      bool
}

// ErrGone is Apple or Google saying a token is no longer valid: APNs 410, FCM UNREGISTERED.
var ErrGone = errors.New("the push token is no longer valid")

type sender interface {
	send(ctx context.Context, p devices.Push, n Note) error
}

// Sender sends to the devices of the platforms it has credentials for.
type Sender struct {
	store     devices.Store
	platforms map[string]sender
	Log       func(format string, args ...any)
}

// FromConfig is the sender config.yaml's credentials make, nil when none are named (push is off). A named file that cannot be read,
// or a platform named without the rest of its keys, is an error.
func FromConfig(cfg usage.Config, store devices.Store) (*Sender, error) {
	s := &Sender{store: store, platforms: map[string]sender{}}
	str := func(k string) string { v, _ := cfg[k].(string); return v }
	if path := str(KeyAPNsKey); path != "" {
		keyID, teamID, topic := str(KeyAPNsKeyID), str(KeyAPNsTeamID), str(KeyAPNsTopic)
		if keyID == "" || teamID == "" {
			return nil, fmt.Errorf("%s needs %s and %s too", KeyAPNsKey, KeyAPNsKeyID, KeyAPNsTeamID)
		}
		if topic == "" {
			topic = defaultTopic
		}
		a, err := newAPNs(path, keyID, teamID, topic)
		if err != nil {
			return nil, err
		}
		s.platforms["apns"] = a
	}
	if path := str(KeyFCM); path != "" {
		f, err := newFCM(path)
		if err != nil {
			return nil, err
		}
		s.platforms["fcm"] = f
	}
	if len(s.platforms) == 0 {
		return nil, nil
	}
	return s, nil
}

// On is whether s sends to platform's devices.
func (s *Sender) On(platform string) bool { return s != nil && s.platforms[platform] != nil }

// Notify sends n to every device with a push token of a platform s has credentials for. A token Apple or Google says is no longer
// valid is dropped; another failure is logged and the rest are still sent. The error is only the store's.
func (s *Sender) Notify(ctx context.Context, n Note) error {
	ds, err := s.store.List()
	if err != nil {
		return err
	}
	for _, d := range ds {
		if d.Push == nil || s.platforms[d.Push.Platform] == nil {
			continue
		}
		sctx, cancel := context.WithTimeout(ctx, sendTimeout)
		err := s.platforms[d.Push.Platform].send(sctx, *d.Push, n)
		cancel()
		switch {
		case errors.Is(err, ErrGone):
			if err := s.store.DropPush(d.ID, d.Push.Token); err != nil {
				return err
			}
			s.log("push: %s's token is no longer valid, dropped", d.Name)
		case err != nil:
			s.log("push: to %s: %v", d.Name, err)
		}
	}
	return nil
}

func (s *Sender) log(format string, args ...any) {
	if s.Log != nil {
		s.Log(format, args...)
	}
}

// privateKey is the PKCS#8 key in the PEM text pem.
func privateKey(text []byte) (crypto.Signer, error) {
	b, _ := pem.Decode(text)
	if b == nil {
		return nil, errors.New("no PEM key in it")
	}
	k, err := x509.ParsePKCS8PrivateKey(b.Bytes)
	if err != nil {
		return nil, err
	}
	signer, ok := k.(crypto.Signer)
	if !ok {
		return nil, errors.New("not a signing key")
	}
	return signer, nil
}

func readKey(path string) (crypto.Signer, error) {
	text, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	k, err := privateKey(text)
	if err != nil {
		return nil, fmt.Errorf("%s: %v", path, err)
	}
	return k, nil
}

// jwt is the signed token of header and claims; sign signs its first two parts.
func jwt(header, claims map[string]any, sign func([]byte) ([]byte, error)) (string, error) {
	h, _ := json.Marshal(header)
	c, _ := json.Marshal(claims)
	enc := base64.RawURLEncoding
	input := enc.EncodeToString(h) + "." + enc.EncodeToString(c)
	sig, err := sign([]byte(input))
	if err != nil {
		return "", err
	}
	return input + "." + enc.EncodeToString(sig), nil
}

// answered is the error a push service's reply makes: what its body says, cut to a line.
func answered(service string, r *http.Response, body []byte) error {
	const most = 200
	s := string(body)
	if len(s) > most {
		s = s[:most]
	}
	return fmt.Errorf("%s answered %d: %s", service, r.StatusCode, s)
}

// unsent is the error of a request that got no answer, without its URL: an APNs URL ends in the device's token.
func unsent(service string, err error) error {
	var u *url.Error
	if errors.As(err, &u) {
		err = u.Err
	}
	return fmt.Errorf("%s: %v", service, err)
}
