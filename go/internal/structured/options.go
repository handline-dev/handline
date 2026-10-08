package structured

import (
	"context"
	"fmt"
)

// Settings is the CLI's effective settings (control request get_settings): the daemon reads an option back before it says the
// option applied, because the CLI answers success for some values it then ignores.
func (s *Session) Settings(ctx context.Context) (map[string]any, error) {
	r, err := s.control(ctx, map[string]any{"subtype": "get_settings"})
	if err != nil {
		return nil, err
	}
	eff, _ := r["effective"].(map[string]any)
	if eff == nil {
		return nil, fmt.Errorf("claude answered get_settings without effective settings")
	}
	return eff, nil
}

// SetModel changes the model of the live session and reads it back.
func (s *Session) SetModel(ctx context.Context, model string) error {
	if model == "" {
		return fmt.Errorf("a model is a name")
	}
	if _, err := s.control(ctx, map[string]any{"subtype": "set_model", "model": model}); err != nil {
		return err
	}
	eff, err := s.Settings(ctx)
	if err != nil {
		return err
	}
	if got, _ := eff["model"].(string); got != model {
		return fmt.Errorf("claude kept model %q after set_model %q", got, model)
	}
	return nil
}

// SetPermissionMode changes the permission mode (default, acceptEdits, plan, ...) and reads it back.
func (s *Session) SetPermissionMode(ctx context.Context, mode string) error {
	if mode == "" {
		return fmt.Errorf("a permission mode is a name")
	}
	if _, err := s.control(ctx, map[string]any{"subtype": "set_permission_mode", "mode": mode}); err != nil {
		return err
	}
	eff, err := s.Settings(ctx)
	if err != nil {
		return err
	}
	perms, _ := eff["permissions"].(map[string]any)
	if got, _ := perms["defaultMode"].(string); got != mode {
		return fmt.Errorf("claude kept permission mode %q after set_permission_mode %q", got, mode)
	}
	return nil
}
