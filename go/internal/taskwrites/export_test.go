package taskwrites

import "github.com/handline/handline/go/internal/authority"

// What the fixture test (package taskwrites_test, which may import reads) borrows of this package's test doubles.

func AuthAs(agent string) authority.Authority { return auth{agent} }

func NewFakeTeller() Teller { return &fakeTeller{} }
