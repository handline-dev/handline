package push

import (
	"context"
	"crypto"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"math/big"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/handline/handline/go/internal/devices"
	"github.com/handline/handline/go/internal/usage"
)

// seen is one request a fake push service got.
type seen struct {
	path   string
	proto  int
	header http.Header
	body   map[string]any
	form   url.Values
}

// fake is a push service over TLS and HTTP/2 that records each request and answers with answer (200 and {} when it is nil).
type fake struct {
	*httptest.Server
	mu     sync.Mutex
	got    []seen
	answer func(r *http.Request) (int, string)
}

func newFake(t *testing.T) *fake {
	f := &fake{}
	f.Server = httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		s := seen{path: r.URL.Path, proto: r.ProtoMajor, header: r.Header}
		if strings.HasPrefix(r.Header.Get("content-type"), "application/x-www-form-urlencoded") {
			s.form, _ = url.ParseQuery(string(raw))
		} else {
			json.Unmarshal(raw, &s.body)
		}
		f.mu.Lock()
		f.got = append(f.got, s)
		answer := f.answer
		f.mu.Unlock()
		code, body := 200, "{}"
		if answer != nil {
			code, body = answer(r)
		}
		w.WriteHeader(code)
		io.WriteString(w, body)
	}))
	f.EnableHTTP2 = true
	f.StartTLS()
	t.Cleanup(f.Close)
	return f
}

func (f *fake) answerWith(answer func(r *http.Request) (int, string)) {
	f.mu.Lock()
	f.answer = answer
	f.mu.Unlock()
}

func (f *fake) take() []seen {
	f.mu.Lock()
	defer f.mu.Unlock()
	got := f.got
	f.got = nil
	return got
}

func writePEM(t *testing.T, key any) string {
	der, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	return string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der}))
}

func must(b []byte, err error) []byte {
	if err != nil {
		panic(err)
	}
	return b
}

func writeFile(t *testing.T, name, text string) string {
	path := filepath.Join(t.TempDir(), name)
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

// claimsOf checks token's signature with verify and is its header and claims.
func claimsOf(t *testing.T, token string, verify func(input, sig []byte) bool) (header, claims map[string]any) {
	t.Helper()
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		t.Fatalf("not a JWT: %q", token)
	}
	enc := base64.RawURLEncoding
	sig, _ := enc.DecodeString(parts[2])
	if !verify([]byte(parts[0]+"."+parts[1]), sig) {
		t.Fatalf("the signature does not verify: %q", token)
	}
	h, _ := enc.DecodeString(parts[0])
	c, _ := enc.DecodeString(parts[1])
	json.Unmarshal(h, &header)
	json.Unmarshal(c, &claims)
	return header, claims
}

// phones is a store with a production APNs device, a sandbox APNs one, an FCM one and one that gave no token.
func phones(t *testing.T) *devices.MemStore {
	s := devices.NewMemStore()
	for i, p := range []*devices.Push{
		{Platform: "apns", Token: "aaa"}, {Platform: "apns", Token: "bbb", Sandbox: true}, {Platform: "fcm", Token: "ccc"}, nil,
	} {
		id := fmt.Sprintf("dev_%d", i)
		s.Add(devices.Device{ID: id, Name: "phone " + id}, "hash"+id)
		if p != nil {
			s.SetPush(id, *p)
		}
	}
	return s
}

func tokens(t *testing.T, s *devices.MemStore) map[string]string {
	ds, err := s.List()
	if err != nil {
		t.Fatal(err)
	}
	out := map[string]string{}
	for _, d := range ds {
		if d.Push != nil {
			out[d.ID] = d.Push.Token
		}
	}
	return out
}

var open = Note{Title: "Decide · bob", Body: "Ship the build?", Data: map[string]string{"kind": "task", "id": "7"}, Collapse: "task:7", Badge: 3}

// With no credentials named push is off; a platform named without the rest of its keys, or a file that cannot be read, is an error.
func TestNoCredentialsMeansPushIsOff(t *testing.T) {
	s, err := FromConfig(usage.Config{"keep_awake": "on"}, devices.NewMemStore())
	if s != nil || err != nil || s.On("apns") || s.On("fcm") {
		t.Fatalf("no credentials: %v %v", s, err)
	}
	ec, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	rk, _ := rsa.GenerateKey(rand.Reader, 2048)
	key, rsaKey := writeFile(t, "key.p8", writePEM(t, ec)), writePEM(t, rk)
	account := func(drop string) string {
		a := map[string]string{"project_id": "p", "client_email": "e", "token_uri": "https://oauth", "private_key": rsaKey}
		delete(a, drop)
		text, _ := json.Marshal(a)
		return writeFile(t, "sa.json", string(text))
	}
	for name, cfg := range map[string]usage.Config{
		"no key id":        {KeyAPNsKey: key, KeyAPNsTeamID: "TEAM"},
		"no team id":       {KeyAPNsKey: key, KeyAPNsKeyID: "KID"},
		"no key file":      {KeyAPNsKey: key + ".missing", KeyAPNsKeyID: "KID", KeyAPNsTeamID: "TEAM"},
		"not a key":        {KeyAPNsKey: writeFile(t, "bad.p8", "hello"), KeyAPNsKeyID: "KID", KeyAPNsTeamID: "TEAM"},
		"no account file":  {KeyFCM: key + ".missing"},
		"account not JSON": {KeyFCM: key},
		"no project":       {KeyFCM: account("project_id")},
		"no email":         {KeyFCM: account("client_email")},
		"no token uri":     {KeyFCM: account("token_uri")},
		"no account key":   {KeyFCM: account("private_key")},
		"account key is EC": {KeyFCM: writeFile(t, "ec.json", `{"project_id":"p","client_email":"e","token_uri":"https://oauth","private_key":`+
			string(must(json.Marshal(writePEM(t, ec))))+`}`)},
		"APNs key is RSA": {KeyAPNsKey: writeFile(t, "rsa.p8", rsaKey), KeyAPNsKeyID: "KID", KeyAPNsTeamID: "TEAM"},
	} {
		if s, err := FromConfig(cfg, devices.NewMemStore()); s != nil || err == nil {
			t.Errorf("%s: %v %v", name, s, err)
		}
	}
	if s, err := FromConfig(usage.Config{KeyFCM: account("")}, devices.NewMemStore()); err != nil || !s.On("fcm") {
		t.Fatalf("a whole service account: %v %v", s, err)
	}
	s, err = FromConfig(usage.Config{KeyAPNsKey: key, KeyAPNsKeyID: "KID", KeyAPNsTeamID: "TEAM"}, devices.NewMemStore())
	if err != nil || !s.On("apns") || s.On("fcm") {
		t.Fatalf("APNs credentials: %v %v", s, err)
	}
}

// Each APNs device gets the note at its own host (a sandbox token at the sandbox), with a provider token signed by the .p8 key that is
// made again after 50 minutes. A silent note carries the badge alone. A token APNs answers 410 for is dropped; another failure keeps it.
// An FCM device is left alone when only APNs has credentials.
func TestAPNsSendsEachNoteAndDropsATokenItAnswers410For(t *testing.T) {
	prod, sandbox := newFake(t), newFake(t)
	ec, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	store := phones(t)
	s, err := FromConfig(usage.Config{KeyAPNsKey: writeFile(t, "key.p8", writePEM(t, ec)), KeyAPNsKeyID: "KID", KeyAPNsTeamID: "TEAM"}, store)
	if err != nil {
		t.Fatal(err)
	}
	var logged []string
	s.Log = func(format string, args ...any) { logged = append(logged, fmt.Sprintf(format, args...)) }
	a := s.platforms["apns"].(*apns)
	clock := time.Unix(1_800_000_000, 0)
	a.client, a.host, a.sandboxHost, a.now = prod.Client(), prod.URL, sandbox.URL, func() time.Time { return clock }
	verify := func(input, sig []byte) bool {
		sum := sha256.Sum256(input)
		return len(sig) == 64 && ecdsa.Verify(&ec.PublicKey, sum[:], new(big.Int).SetBytes(sig[:32]), new(big.Int).SetBytes(sig[32:]))
	}

	if err := s.Notify(context.Background(), open); err != nil {
		t.Fatal(err)
	}
	p, sb := prod.take(), sandbox.take()
	if len(p) != 1 || p[0].path != "/3/device/aaa" || len(sb) != 1 || sb[0].path != "/3/device/bbb" {
		t.Fatalf("one note per APNs device, at its host: %+v %+v", p, sb)
	}
	r := p[0]
	if r.proto != 2 {
		t.Errorf("APNs is HTTP/2, got %d", r.proto)
	}
	for k, want := range map[string]string{"apns-topic": "dev.handline.mobile", "apns-push-type": "alert", "apns-priority": "10", "apns-collapse-id": "task:7"} {
		if got := r.header.Get(k); got != want {
			t.Errorf("%s = %q, want %q", k, got, want)
		}
	}
	bearer := strings.TrimPrefix(r.header.Get("authorization"), "bearer ")
	h, c := claimsOf(t, bearer, verify)
	if h["alg"] != "ES256" || h["kid"] != "KID" || c["iss"] != "TEAM" || c["iat"] != float64(clock.Unix()) {
		t.Errorf("provider token: %v %v", h, c)
	}
	aps, _ := r.body["aps"].(map[string]any)
	alert, _ := aps["alert"].(map[string]any)
	if alert["title"] != "Decide · bob" || alert["body"] != "Ship the build?" || aps["badge"] != float64(3) || r.body["kind"] != "task" || r.body["id"] != "7" {
		t.Errorf("payload: %v", r.body)
	}

	clock = clock.Add(49 * time.Minute)
	if err := s.Notify(context.Background(), Note{Badge: 2, Silent: true}); err != nil {
		t.Fatal(err)
	}
	r = prod.take()[0]
	sandbox.take()
	if got := strings.TrimPrefix(r.header.Get("authorization"), "bearer "); got != bearer {
		t.Error("a provider token under 50 minutes old is used again")
	}
	if aps, _ := r.body["aps"].(map[string]any); len(aps) != 1 || aps["badge"] != float64(2) || r.header.Get("apns-priority") != "5" || r.header.Get("apns-collapse-id") != "" {
		t.Errorf("a silent note is the badge alone: %v %v", r.body, r.header)
	}

	clock = clock.Add(2 * time.Minute)
	prod.answerWith(func(*http.Request) (int, string) { return 410, `{"reason":"Unregistered"}` })
	sandbox.answerWith(func(*http.Request) (int, string) { return 500, `{"reason":"InternalServerError"}` })
	if err := s.Notify(context.Background(), open); err != nil {
		t.Fatal(err)
	}
	r = prod.take()[0]
	if got := strings.TrimPrefix(r.header.Get("authorization"), "bearer "); got == bearer {
		t.Error("a provider token 51 minutes old is made again")
	} else if _, c := claimsOf(t, got, verify); c["iat"] != float64(clock.Unix()) {
		t.Errorf("the new provider token: %v", c)
	}
	if got := tokens(t, store); len(got) != 2 || got["dev_1"] != "bbb" || got["dev_2"] != "ccc" {
		t.Fatalf("the token APNs answered 410 for is dropped, the others kept: %v", got)
	}
	if len(logged) != 2 || !strings.Contains(strings.Join(logged, "\n"), "APNs answered 500") {
		t.Errorf("logged: %q", logged)
	}
}

// An FCM device gets the note through the v1 API with an access token traded for a JWT the service account signed, once for several
// notes. A silent note is data alone. A token FCM answers UNREGISTERED for is dropped; another refusal keeps it.
func TestFCMSendsEachNoteAndDropsAnUnregisteredToken(t *testing.T) {
	fcmFake, oauth := newFake(t), newFake(t)
	oauth.answerWith(func(*http.Request) (int, string) { return 200, `{"access_token":"at1","expires_in":3600}` })
	rk, _ := rsa.GenerateKey(rand.Reader, 2048)
	acct, _ := json.Marshal(map[string]string{"type": "service_account", "project_id": "proj", "client_email": "push@proj.iam.gserviceaccount.com",
		"private_key": writePEM(t, rk), "token_uri": oauth.URL + "/token"})
	store := phones(t)
	s, err := FromConfig(usage.Config{KeyFCM: writeFile(t, "sa.json", string(acct))}, store)
	if err != nil || s.On("apns") || !s.On("fcm") {
		t.Fatal(s, err)
	}
	f := s.platforms["fcm"].(*fcm)
	clock := time.Unix(1_800_000_000, 0)
	f.host, f.now = fcmFake.URL, func() time.Time { return clock }
	f.client = fcmFake.Client() // both fakes share httptest's certificate

	if err := s.Notify(context.Background(), open); err != nil {
		t.Fatal(err)
	}
	if err := s.Notify(context.Background(), Note{Badge: 2, Silent: true, Data: map[string]string{"kind": "task", "id": "7"}}); err != nil {
		t.Fatal(err)
	}
	trades := oauth.take()
	if len(trades) != 1 || trades[0].path != "/token" || trades[0].form.Get("grant_type") != "urn:ietf:params:oauth:grant-type:jwt-bearer" {
		t.Fatalf("one token trade for two notes: %+v", trades)
	}
	h, c := claimsOf(t, trades[0].form.Get("assertion"), func(input, sig []byte) bool {
		sum := sha256.Sum256(input)
		return rsa.VerifyPKCS1v15(&rk.PublicKey, crypto.SHA256, sum[:], sig) == nil
	})
	if h["alg"] != "RS256" || c["iss"] != "push@proj.iam.gserviceaccount.com" || c["aud"] != oauth.URL+"/token" || c["scope"] != fcmScope ||
		c["exp"] != float64(clock.Add(time.Hour).Unix()) {
		t.Errorf("assertion: %v %v", h, c)
	}
	sent := fcmFake.take()
	if len(sent) != 2 || sent[0].path != "/v1/projects/proj/messages:send" || sent[0].header.Get("authorization") != "Bearer at1" {
		t.Fatalf("one message per note, as the project, with the access token: %+v", sent)
	}
	msg, _ := sent[0].body["message"].(map[string]any)
	shown, _ := msg["notification"].(map[string]any)
	data, _ := msg["data"].(map[string]any)
	android, _ := msg["android"].(map[string]any)
	an, _ := android["notification"].(map[string]any)
	if msg["token"] != "ccc" || shown["title"] != "Decide · bob" || shown["body"] != "Ship the build?" || data["kind"] != "task" || data["id"] != "7" ||
		data["badge"] != "3" || android["collapse_key"] != "task:7" || an["tag"] != "task:7" || an["notification_count"] != float64(3) {
		t.Errorf("message: %v", sent[0].body)
	}
	msg, _ = sent[1].body["message"].(map[string]any)
	if data, _ := msg["data"].(map[string]any); msg["notification"] != nil || msg["android"] != nil || data["badge"] != "2" {
		t.Errorf("a silent note is data alone: %v", sent[1].body)
	}

	fcmFake.answerWith(func(*http.Request) (int, string) {
		return 400, `{"error":{"code":400,"status":"INVALID_ARGUMENT","details":[{"errorCode":"INVALID_ARGUMENT"}]}}`
	})
	s.Notify(context.Background(), open)
	if got := tokens(t, store); got["dev_2"] != "ccc" {
		t.Fatalf("a refusal that is not UNREGISTERED keeps the token: %v", got)
	}
	fcmFake.answerWith(func(*http.Request) (int, string) {
		return 404, `{"error":{"code":404,"status":"NOT_FOUND","details":[{"@type":"type.googleapis.com/google.firebase.fcm.v1.FcmError","errorCode":"UNREGISTERED"}]}}`
	})
	s.Notify(context.Background(), open)
	if got := tokens(t, store); len(got) != 2 || got["dev_2"] != "" || got["dev_0"] != "aaa" || got["dev_1"] != "bbb" {
		t.Fatalf("the token FCM answered UNREGISTERED for is dropped, the APNs ones kept: %v", got)
	}

	clock = clock.Add(59*time.Minute + time.Second)
	store.SetPush("dev_2", devices.Push{Platform: "fcm", Token: "ddd"})
	fcmFake.answerWith(nil)
	s.Notify(context.Background(), open)
	if len(oauth.take()) != 1 {
		t.Error("an access token in its last minute is traded again")
	}
}
