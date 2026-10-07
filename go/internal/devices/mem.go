package devices

import (
	"sort"
	"sync"
)

// MemStore keeps devices in memory, for tests of what does not need the database.
type MemStore struct {
	mu     sync.Mutex
	byID   map[string]Device
	byHash map[string]string
}

func NewMemStore() *MemStore {
	return &MemStore{byID: map[string]Device{}, byHash: map[string]string{}}
}

func (s *MemStore) Add(d Device, tokenHash string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.byID[d.ID], s.byHash[tokenHash] = d, d.ID
	return nil
}

func (s *MemStore) ByTokenHash(hash string) (Device, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	id, ok := s.byHash[hash]
	return s.byID[id], ok, nil
}

func (s *MemStore) Delete(id string) (bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.byID[id]; !ok {
		return false, nil
	}
	delete(s.byID, id)
	for h, i := range s.byHash {
		if i == id {
			delete(s.byHash, h)
		}
	}
	return true, nil
}

func (s *MemStore) List() ([]Device, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Device, 0, len(s.byID))
	for _, d := range s.byID {
		out = append(out, d)
	}
	sort.Slice(out, func(i, j int) bool {
		return out[i].Created < out[j].Created || out[i].Created == out[j].Created && out[i].ID < out[j].ID
	})
	return out, nil
}

func (s *MemStore) Touch(id string, ts int64) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if d, ok := s.byID[id]; ok {
		d.LastSeen = &ts
		s.byID[id] = d
	}
	return nil
}
