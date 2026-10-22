//go:build linux

package main

import (
	"bufio"
	"os"
	"regexp"
	"strconv"
	"strings"
	"syscall"
)

const isDarwin = false

var octal = regexp.MustCompile(`\\([0-7]{3})`)

// mountinfo reads /proc/self/mountinfo in its own order; nil if it cannot be read.
func mountinfo() []mount {
	f, err := os.Open("/proc/self/mountinfo")
	if err != nil {
		return nil
	}
	defer f.Close()
	var out []mount
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 64*1024), 1<<20)
	for sc.Scan() {
		left, right, ok := strings.Cut(sc.Text(), " - ")
		if !ok {
			continue
		}
		fields := strings.Fields(left)
		rest := strings.Fields(right)
		if len(fields) < 6 || len(rest) < 2 {
			continue
		}
		mp := octal.ReplaceAllStringFunc(fields[4], func(s string) string {
			n, _ := strconv.ParseUint(s[1:], 8, 8)
			return string(rune(n))
		})
		ro := false
		for _, o := range strings.Split(fields[5], ",") {
			if o == "ro" {
				ro = true
			}
		}
		out = append(out, mount{path: mp, fstype: rest[0], source: rest[1], readonly: ro})
	}
	return out
}

func allMounts() []mount { return mountinfo() }

func fsUsage(path string) *Usage {
	var s syscall.Statfs_t
	if err := syscall.Statfs(path, &s); err != nil {
		return nil
	}
	fr := int64(s.Frsize)
	if fr == 0 {
		fr = int64(s.Bsize)
	}
	return &Usage{Total: int64(s.Blocks) * fr, Free: int64(s.Bavail) * fr, Used: (int64(s.Blocks) - int64(s.Bfree)) * fr}
}
