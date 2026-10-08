package usage

import (
	"math"
	"testing"
)

// The want figures are what handline/usage/prices.py says for the same tokens.
func TestCostMatchesThePythonTable(t *testing.T) {
	tok := Tokens{Inp: 1000, CC: 2000, CR: 300000, Out: 500, Thinking: 200}
	for model, want := range map[string]float64{
		"claude-opus-5-5-x": 0.084, "claude-opus-4-1": 0.18, "claude-sonnet-5": 0.072, "claude-haiku-5-5": 0.036,
		"weird": 0.108, "claude-fable-5-1": 0.135, "Mythos-5": 0.36,
	} {
		if got := Cost(model, tok, nil); math.Abs(got-want) > 1e-12 {
			t.Errorf("%s: %v, want %v", model, got, want)
		}
	}
}

func TestThinkingIsPricedOnceAndNeverMoreThanOut(t *testing.T) {
	by := ClassCosts("claude-sonnet-5", Tokens{Out: 100, Thinking: 400}, nil)
	if by["out"] != 0 || math.Abs(by["thinking"]-100*5*2/1e6) > 1e-15 {
		t.Fatalf("%v", by)
	}
}

func TestConfigPricesLayOverTheTable(t *testing.T) {
	table := PricesFromConfig(Config{"token_prices": map[string]any{"zzz": 7, "opus-5-5": 8.0, "bad": 0, "flag": true, "text": "x"}})
	if table[0].Pattern != "zzz" || table[0].USD != 7 || len(table) != len(InputPrices)+1 {
		t.Fatalf("a pattern the table lacks goes first: %v", table)
	}
	if priceOf(table, "opus-5-5") != 8 || PatternOf("an-zzz", table) != "zzz" {
		t.Fatalf("%v", table)
	}
	if got := Cost("x-opus-5-5", Tokens{10, 10, 10, 10, 10}, table); math.Abs(got-0.000584) > 1e-12 {
		t.Fatalf("%v", got)
	}
	if len(PricesFromConfig(Config{})) != len(InputPrices) {
		t.Fatal("no entry, no change")
	}
}
