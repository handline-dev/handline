package push

import (
	"bytes"
	"context"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/handline/handline/go/internal/devices"
)

const fcmScope = "https://www.googleapis.com/auth/firebase.messaging"

// fcm sends through FCM's HTTP v1 API as a service account: a signed JWT is traded at the account's token_uri for an access token.
type fcm struct {
	project, email, tokenURI string
	key                      *rsa.PrivateKey
	client                   *http.Client
	host                     string
	now                      func() time.Time

	mu     sync.Mutex
	access string
	until  time.Time
}

func newFCM(path string) (*fcm, error) {
	text, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var acct struct {
		ProjectID   string `json:"project_id"`
		PrivateKey  string `json:"private_key"`
		ClientEmail string `json:"client_email"`
		TokenURI    string `json:"token_uri"`
	}
	if err := json.Unmarshal(text, &acct); err != nil {
		return nil, fmt.Errorf("%s: %v", path, err)
	}
	if acct.ProjectID == "" || acct.ClientEmail == "" || acct.TokenURI == "" {
		return nil, fmt.Errorf("%s: a service account JSON has project_id, client_email and token_uri", path)
	}
	k, err := privateKey([]byte(acct.PrivateKey))
	if err != nil {
		return nil, fmt.Errorf("%s: %v", path, err)
	}
	rk, ok := k.(*rsa.PrivateKey)
	if !ok {
		return nil, fmt.Errorf("%s: a service account key is an RSA key", path)
	}
	return &fcm{project: acct.ProjectID, email: acct.ClientEmail, tokenURI: acct.TokenURI, key: rk, client: &http.Client{},
		host: "https://fcm.googleapis.com", now: time.Now}, nil
}

// bearer is the access token, traded again a minute before it runs out.
func (f *fcm) bearer(ctx context.Context) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	now := f.now()
	if f.access != "" && now.Before(f.until.Add(-time.Minute)) {
		return f.access, nil
	}
	claims := map[string]any{"iss": f.email, "scope": fcmScope, "aud": f.tokenURI, "iat": now.Unix(), "exp": now.Add(time.Hour).Unix()}
	assertion, err := jwt(map[string]any{"alg": "RS256", "typ": "JWT"}, claims, func(in []byte) ([]byte, error) {
		sum := sha256.Sum256(in)
		return rsa.SignPKCS1v15(rand.Reader, f.key, crypto.SHA256, sum[:])
	})
	if err != nil {
		return "", err
	}
	form := url.Values{"grant_type": {"urn:ietf:params:oauth:grant-type:jwt-bearer"}, "assertion": {assertion}}
	req, err := http.NewRequestWithContext(ctx, "POST", f.tokenURI, strings.NewReader(form.Encode()))
	if err != nil {
		return "", err
	}
	req.Header.Set("content-type", "application/x-www-form-urlencoded")
	r, err := f.client.Do(req)
	if err != nil {
		return "", unsent("FCM token", err)
	}
	defer r.Body.Close()
	answer, _ := io.ReadAll(io.LimitReader(r.Body, 4096))
	var got struct {
		AccessToken string `json:"access_token"`
		ExpiresIn   int64  `json:"expires_in"`
	}
	if r.StatusCode != http.StatusOK || json.Unmarshal(answer, &got) != nil || got.AccessToken == "" {
		return "", answered("FCM token", r, answer)
	}
	f.access, f.until = got.AccessToken, now.Add(time.Duration(got.ExpiresIn)*time.Second)
	return f.access, nil
}

func (f *fcm) send(ctx context.Context, p devices.Push, n Note) error {
	data := map[string]string{"badge": strconv.Itoa(n.Badge)}
	for k, v := range n.Data {
		data[k] = v
	}
	msg := map[string]any{"token": p.Token, "data": data}
	if !n.Silent {
		msg["notification"] = map[string]string{"title": n.Title, "body": n.Body}
		shown := map[string]any{"notification_count": n.Badge}
		android := map[string]any{"priority": "high", "notification": shown}
		if n.Collapse != "" {
			android["collapse_key"], shown["tag"] = n.Collapse, n.Collapse
		}
		msg["android"] = android
	}
	body, _ := json.Marshal(map[string]any{"message": msg})
	bearer, err := f.bearer(ctx)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, "POST", f.host+"/v1/projects/"+f.project+"/messages:send", bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("authorization", "Bearer "+bearer)
	req.Header.Set("content-type", "application/json")
	r, err := f.client.Do(req)
	if err != nil {
		return unsent("FCM", err)
	}
	defer r.Body.Close()
	answer, _ := io.ReadAll(io.LimitReader(r.Body, 4096))
	if r.StatusCode == http.StatusOK {
		return nil
	}
	var e struct {
		Error struct {
			Details []struct {
				ErrorCode string `json:"errorCode"`
			} `json:"details"`
		} `json:"error"`
	}
	json.Unmarshal(answer, &e)
	for _, d := range e.Error.Details {
		if d.ErrorCode == "UNREGISTERED" {
			return ErrGone
		}
	}
	return answered("FCM", r, answer)
}
