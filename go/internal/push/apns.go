package push

import (
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/rand"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"sync"
	"time"

	"github.com/handline/handline/go/internal/devices"
)

// apnsTokenAge is how long one provider token is used: Apple takes one up to an hour old and refuses a new one more often than every 20
// minutes.
const apnsTokenAge = 50 * time.Minute

// apns sends through Apple's HTTP/2 API with token auth: an ES256 token signed with the .p8 key.
type apns struct {
	key                  *ecdsa.PrivateKey
	keyID, teamID, topic string
	client               *http.Client
	host, sandboxHost    string
	now                  func() time.Time

	mu    sync.Mutex
	token string
	made  time.Time
}

func newAPNs(path, keyID, teamID, topic string) (*apns, error) {
	k, err := readKey(path)
	if err != nil {
		return nil, err
	}
	ec, ok := k.(*ecdsa.PrivateKey)
	if !ok {
		return nil, fmt.Errorf("%s: an APNs key is an EC key", path)
	}
	return &apns{key: ec, keyID: keyID, teamID: teamID, topic: topic, client: &http.Client{},
		host: "https://api.push.apple.com", sandboxHost: "https://api.sandbox.push.apple.com", now: time.Now}, nil
}

// bearer is the provider token, made again once it is apnsTokenAge old.
func (a *apns) bearer() (string, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.token != "" && a.now().Sub(a.made) < apnsTokenAge {
		return a.token, nil
	}
	now := a.now()
	t, err := jwt(map[string]any{"alg": "ES256", "kid": a.keyID}, map[string]any{"iss": a.teamID, "iat": now.Unix()}, func(in []byte) ([]byte, error) {
		sum := sha256.Sum256(in)
		r, s, err := ecdsa.Sign(rand.Reader, a.key, sum[:])
		if err != nil {
			return nil, err
		}
		sig := make([]byte, 64)
		r.FillBytes(sig[:32])
		s.FillBytes(sig[32:])
		return sig, nil
	})
	if err != nil {
		return "", err
	}
	a.token, a.made = t, now
	return t, nil
}

func (a *apns) send(ctx context.Context, p devices.Push, n Note) error {
	aps := map[string]any{"badge": n.Badge}
	if !n.Silent {
		aps["alert"] = map[string]string{"title": n.Title, "body": n.Body}
		aps["sound"] = "default"
	}
	payload := map[string]any{"aps": aps}
	for k, v := range n.Data {
		payload[k] = v
	}
	body, _ := json.Marshal(payload)
	host := a.host
	if p.Sandbox {
		host = a.sandboxHost
	}
	req, err := http.NewRequestWithContext(ctx, "POST", host+"/3/device/"+p.Token, bytes.NewReader(body))
	if err != nil {
		return err
	}
	bearer, err := a.bearer()
	if err != nil {
		return err
	}
	req.Header.Set("authorization", "bearer "+bearer)
	req.Header.Set("apns-topic", a.topic)
	req.Header.Set("apns-push-type", "alert")
	if n.Silent {
		req.Header.Set("apns-priority", "5")
	} else {
		req.Header.Set("apns-priority", "10")
	}
	if n.Collapse != "" {
		req.Header.Set("apns-collapse-id", n.Collapse)
	}
	r, err := a.client.Do(req)
	if err != nil {
		return unsent("APNs", err)
	}
	defer r.Body.Close()
	answer, _ := io.ReadAll(io.LimitReader(r.Body, 4096))
	switch r.StatusCode {
	case http.StatusOK:
		return nil
	case http.StatusGone:
		return ErrGone
	}
	return answered("APNs", r, answer)
}
