//go:build darwin || freebsd || netbsd || openbsd

package main

import (
	"os/exec"
	"regexp"
	"runtime"
	"strings"
	"syscall"
)

var isDarwin = runtime.GOOS == "darwin"

func mountinfo() []mount { return nil }

var mountLine = regexp.MustCompile(`^(\S+) on (.+) \(([^,)]+)(.*)\)$`)

// allMounts parses `mount`, as agent.py does where there is no /proc.
func allMounts() []mount {
	out, err := exec.Command("mount").Output()
	if err != nil {
		return nil
	}
	var found []mount
	for _, line := range strings.Split(string(out), "\n") {
		if m := mountLine.FindStringSubmatch(line); m != nil {
			found = append(found, mount{path: m[2], source: m[1], fstype: m[3], readonly: strings.Contains(m[4], "read-only")})
		}
	}
	return found
}

func fsUsage(path string) *Usage {
	var s syscall.Statfs_t
	if err := syscall.Statfs(path, &s); err != nil {
		return nil
	}
	b := int64(s.Bsize)
	return &Usage{Total: int64(s.Blocks) * b, Free: int64(s.Bavail) * b, Used: (int64(s.Blocks) - int64(s.Bfree)) * b}
}
