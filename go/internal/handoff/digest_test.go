package handoff

import (
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"
)

// testdata/digest_cases.json holds the inputs and what the Python digest module answered for them.
type digestCases struct {
	Korean, Bad, Nohdr, Missing string
	Cases                       struct {
		KoreanParse struct {
			Date, Agent, Updated string
			Ft                   int
			Sections             map[string][]string
			Facts                string
			Late                 []string
		} `json:"korean_parse"`
		KoreanMigrated string    `json:"korean_migrated"`
		KoreanRender   string    `json:"korean_render"`
		KoreanValid    [2]any    `json:"korean_valid"`
		Rolled         string    `json:"rolled"`
		BadValidate    [2]any    `json:"bad_validate"`
		Nohdr          [2][2]any `json:"nohdr"`
		Missing        [2]any    `json:"missing"`
	}
}

func load(t *testing.T) digestCases {
	b, err := os.ReadFile("testdata/digest_cases.json")
	if err != nil {
		t.Fatal(err)
	}
	var c digestCases
	if err := json.Unmarshal(b, &c); err != nil {
		t.Fatal(err)
	}
	return c
}

func problemsOf(v any) []string {
	out := []string{}
	for _, p := range v.([]any) {
		out = append(out, p.(string))
	}
	return out
}

func TestADigestReadsLikePython(t *testing.T) {
	c := load(t)
	d := ParseDigest(c.Korean)
	want := c.Cases.KoreanParse
	if d.Date != want.Date || d.Agent != want.Agent || d.Updated != want.Updated || d.FoldedThrough != want.Ft || d.Facts != want.Facts ||
		!reflect.DeepEqual(d.Sections, want.Sections) || !reflect.DeepEqual(d.Late, want.Late) {
		t.Fatalf("parsed %+v, want %+v", d, want)
	}
	if got := MigrateText(c.Korean); got != c.Cases.KoreanMigrated {
		t.Errorf("migrated %q", got)
	}
	if got := d.Render(); got != c.Cases.KoreanRender {
		t.Errorf("rendered %q", got)
	}
	if again := ParseDigest(MigrateText(c.Korean)); !reflect.DeepEqual(again, d) {
		t.Errorf("the migrated text is another digest: %+v", again)
	}
	if got := CarryForward(d, "2026-09-16").Render(); got != c.Cases.Rolled {
		t.Errorf("rolled %q", got)
	}
}

func TestAnItemThatSaysAHeadingWordIsNotMigrated(t *testing.T) {
	text := "## 완료\n- 완료 means done\n- ## 완료\n"
	if got := MigrateText(text); got != "## Done\n- 완료 means done\n- ## 완료\n" {
		t.Errorf("got %q", got)
	}
}

func TestValidateAgreesWithPython(t *testing.T) {
	c := load(t)
	d := ParseDigest(c.Korean)
	ok, problems := Validate(c.Korean, nil, false)
	if !ok || c.Cases.KoreanValid[0] != true || len(problems) != 0 {
		t.Errorf("a good digest: %v %v", ok, problems)
	}
	ok, problems = Validate(c.Bad, d, false)
	if ok || !reflect.DeepEqual(problems, problemsOf(c.Cases.BadValidate[1])) {
		t.Errorf("a lossy one: %v %q", ok, problems)
	}
	ok, problems = Validate(c.Nohdr, nil, false)
	if ok || !reflect.DeepEqual(problems, problemsOf(c.Cases.Nohdr[0][1])) {
		t.Errorf("headerless: %v %q", ok, problems)
	}
	if ok, problems = Validate(c.Nohdr, nil, true); !ok || len(problems) != 0 {
		t.Errorf("headerless but stamped: %v %q", ok, problems)
	}
	ok, problems = Validate(c.Missing, nil, false)
	if ok || !reflect.DeepEqual(problems, problemsOf(c.Cases.Missing[1])) {
		t.Errorf("a missing section: %v %q", ok, problems)
	}
	if strings.Contains(strings.Join(problems, ""), "disappeared") {
		t.Error("no previous digest, nothing can disappear")
	}
}

func TestRenderParseRoundTrips(t *testing.T) {
	d := &Digest{Date: "2026-09-15", Agent: "backend", Updated: "14:32 (handoff #3)", FoldedThrough: 7, Facts: "one\ntwo", Late: []string{"x"},
		Sections: emptySections()}
	d.Sections["done"] = []string{"a", "b"}
	d.Sections["next"] = []string{"c"}
	back := ParseDigest(d.Render())
	if !reflect.DeepEqual(back, d) {
		t.Errorf("%+v != %+v", back, d)
	}
	if ParseDigest("").Items("done") == nil {
		t.Error("an empty digest has no sections")
	}
}
