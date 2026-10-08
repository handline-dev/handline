package usage

import (
	"sort"
	"strings"
)

// What a token costs, by model and by kind (usage/prices.py). Only ratios enter a share, so these are list prices in USD per million
// input tokens; output is 5x the input price, a cache write 1.25x and a cache read 0.1x except where CacheReadMult says otherwise.

// priceEntry is one pattern of the model id and its input price. The table runs most specific first: a model is priced by the first
// pattern its id contains.
type priceEntry struct {
	Pattern string
	USD     float64
}

// InputPrices is prices.INPUT_PRICE in table order.
var InputPrices = []priceEntry{
	{"fable-5-1", 10}, {"mythos-5-1", 10}, {"fable", 10}, {"mythos", 10},
	{"opus-5-5", 4}, {"opus", 5}, {"sonnet-5", 2}, {"sonnet", 3}, {"haiku", 1},
}

// DefaultPattern prices a model that matches none.
const DefaultPattern = "sonnet"

// CacheReadMult is prices.CACHE_READ: a cache read as a multiple of the input price where it is not 0.1x.
var CacheReadMult = map[string]float64{"opus-5-5": 0.05, "fable-5-1": 0.025, "mythos-5-1": 0.025}

// Kinds in the order token_calls keeps them, with their multiple of the model's input price (prices.KIND).
var kindMult = []struct {
	Kind string
	Mult float64
}{{"inp", 1}, {"cc", 1.25}, {"cr", 0.1}, {"out", 5}, {"thinking", 5}}

// Tokens is one call's (or one span's) tokens by class.
type Tokens struct{ Inp, CC, CR, Out, Thinking int64 }

// PatternOf is the first pattern of table that model contains, else the default one.
func PatternOf(model string, table []priceEntry) string {
	m := strings.ToLower(model)
	for _, p := range table {
		if strings.Contains(m, p.Pattern) {
			return p.Pattern
		}
	}
	return DefaultPattern
}

// PricesFromConfig is prices.table_from_config: InputPrices with config.yaml's token_prices laid over it. A pattern the table has takes
// the new price where it is; one it lacks goes first, in sorted order (the map has none). An entry that is not a positive number is ignored.
func PricesFromConfig(cfg Config) []priceEntry {
	out := append([]priceEntry(nil), InputPrices...)
	given, _ := cfg["token_prices"].(map[string]any)
	var added []priceEntry
	for _, pat := range sortedKeys(given) {
		f, ok := givenPrice(given[pat])
		if !ok {
			continue
		}
		set := false
		for i := range out {
			if out[i].Pattern == pat {
				out[i].USD, set = f, true
			}
		}
		if !set {
			added = append(added, priceEntry{pat, f})
		}
	}
	return append(added, out...)
}

func givenPrice(v any) (float64, bool) {
	switch n := v.(type) {
	case int:
		return float64(n), n > 0
	case int64:
		return float64(n), n > 0
	case float64:
		return n, n > 0
	}
	return 0, false
}

// ClassCosts is prices.class_costs: USD each kind of these tokens costs at list price, by kind. Thinking is a part of Out, so Out here
// is what is left of it and they are priced once, as thinking.
func ClassCosts(model string, t Tokens, table []priceEntry) map[string]float64 {
	if table == nil {
		table = InputPrices
	}
	pat := PatternOf(model, table)
	base := priceOf(InputPrices, DefaultPattern)
	if p, ok := lookupPrice(table, pat); ok {
		base = p
	}
	think := min(t.Thinking, t.Out)
	counts := map[string]int64{"inp": t.Inp, "cc": t.CC, "cr": t.CR, "out": t.Out - think, "thinking": think}
	out := map[string]float64{}
	for _, k := range kindMult {
		m := k.Mult
		if k.Kind == "cr" {
			if r, ok := CacheReadMult[pat]; ok {
				m = r
			}
		}
		out[k.Kind] = float64(counts[k.Kind]) * m * base / 1_000_000
	}
	return out
}

func lookupPrice(table []priceEntry, pat string) (float64, bool) {
	for _, p := range table {
		if p.Pattern == pat {
			return p.USD, true
		}
	}
	return 0, false
}

func priceOf(table []priceEntry, pat string) float64 { p, _ := lookupPrice(table, pat); return p }

func sortedKeys(m map[string]any) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// Cost is the sum of ClassCosts.
func Cost(model string, t Tokens, table []priceEntry) float64 {
	by := ClassCosts(model, t, table)
	var sum float64
	for _, k := range kindMult { // a fixed order keeps the float sum the same on every run
		sum += by[k.Kind]
	}
	return sum
}
