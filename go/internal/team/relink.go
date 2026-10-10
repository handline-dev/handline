package team

import (
	"os"
	"path/filepath"

	"github.com/handline/handline/go/internal/paths"
)

// sharedItems is what an account of the vendor links from shared/ (handline/vendors shared_items). Nothing credential-shaped is here.
func sharedItems(vendor string) []string {
	if vendor == DefaultVendor {
		return []string{"settings.json", "CLAUDE.md", "skills", "agents", "commands", "plugins"}
	}
	return nil // opencode's config is under XDG_DATA_HOME, which every account already shares
}

// RelinkAll links every registered account to the shared items that gained a target since the account was registered, and returns what it
// linked, by account (accounts.relink_all). Anything already there is left alone, a link elsewhere and a real file or directory included:
// only a human's register replaces those. An account that errors is reported to log and skipped, so the rest are caught up.
func RelinkAll(home string, log func(string)) map[string][]string {
	done := map[string][]string{}
	for _, acct := range paths.ListAccounts(home) {
		vendor := AccountVendor(home, acct)
		if err := CheckVendor(vendor); err != nil {
			log("  relink: " + acct + ": " + err.Error())
			continue
		}
		cdir, _ := AccountConfigDir(home, acct)
		if st, err := os.Stat(cdir); err != nil || !st.IsDir() {
			continue // not an account register ran on
		}
		shared := filepath.Join(home, "shared", ConfigDirName(vendor))
		var linked []string
		var failed error
		for _, item := range sharedItems(vendor) {
			target, link := filepath.Join(shared, item), filepath.Join(cdir, item)
			if _, err := os.Stat(target); err != nil {
				continue
			}
			if _, err := os.Lstat(link); err == nil {
				continue
			}
			if failed = os.Symlink(target, link); failed != nil {
				break
			}
			linked = append(linked, item)
		}
		if failed != nil {
			log("  relink: " + acct + ": " + failed.Error())
			continue
		}
		if len(linked) > 0 {
			done[acct] = linked
		}
	}
	return done
}
