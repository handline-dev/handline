package adminwrites

import (
	"strings"
	"testing"

	"github.com/handline/handline/go/internal/handoff"
)

func TestJoinLinesMakesOneLineOfTheBox(t *testing.T) {
	if got := joinLines("one\ntwo\r\nthree\rfour"); got != "one two three four" {
		t.Errorf("line feed, CRLF and CR: %q", got)
	}
	if got := joinLines("  one  \n\n  two  "); got != "one two" {
		t.Errorf("blank lines and the ends of each line: %q", got)
	}
	if got := joinLines("\n\n"); got != "" {
		t.Errorf("only breaks: %q", got)
	}
	if got := joinLines("already one line"); got != "already one line" {
		t.Errorf("one line is left alone: %q", got)
	}
}

func TestHandoffPromptSettingIsJoinedToOneLine(t *testing.T) {
	v, err := settingValue(handoff.PromptKey, "Write the note\nfor the next session,\r\nbriefly.  ")
	if err != nil {
		t.Fatalf("a prompt typed on several lines was refused: %v", err)
	}
	if want := "Write the note for the next session, briefly."; v != want {
		t.Fatalf("stored %q, want %q", v, want)
	}
}

func TestHandoffPromptSettingRefusesAShortPromptAndTakesABlankOne(t *testing.T) {
	short := strings.Repeat("x", PromptMin-1)
	if _, err := settingValue(handoff.PromptKey, short); err == nil || !strings.Contains(err.Error(), "Too short") {
		t.Fatalf("a %d-character prompt: err = %v, want Too short", len(short), err)
	}
	// Line breaks do not count toward the length.
	if _, err := settingValue(handoff.PromptKey, strings.Repeat("x", PromptMin/2)+"\n\n"+strings.Repeat("y", PromptMin/2-2)); err == nil {
		t.Fatal("a short prompt padded with line breaks was kept")
	}
	if v, err := settingValue(handoff.PromptKey, strings.Repeat("x", PromptMin)); err != nil || v != strings.Repeat("x", PromptMin) {
		t.Fatalf("a %d-character prompt: %v, %v", PromptMin, v, err)
	}
	for _, blank := range []string{"", " \n \r\n "} {
		if v, err := settingValue(handoff.PromptKey, blank); err != nil || v != "" {
			t.Fatalf("a blank prompt %q means the built-in: %v, %v", blank, v, err)
		}
	}
}
