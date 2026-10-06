package main

import "testing"

func TestTheDefaultHomeIsRefusedUnlessAskedFor(t *testing.T) {
	t.Setenv("HANDLINE_HOME", "")
	t.Setenv("OCP_HOME", "")
	if h, err := resolveHome("", false); err == nil {
		t.Fatalf("started on the default home %s", h)
	}
	if h, err := resolveHome("/scratch/h", false); err != nil || h != "/scratch/h" {
		t.Fatalf("%q %v", h, err)
	}
	if _, err := resolveHome("", true); err != nil {
		t.Fatal(err)
	}
}
