package main

// Which filesystems are real drives worth scanning (agent.py's list_mounts),
// and where each path's mount comes from (its root_source).

import (
	"os"
	"sort"
	"strings"
)

type mount struct {
	path, source, fstype string
	readonly             bool
}

var realFS = map[string]bool{}

func init() {
	for _, f := range strings.Fields("ext2 ext3 ext4 xfs btrfs zfs ntfs ntfs3 fuseblk exfat vfat f2fs jfs bcachefs " +
		"hfsplus apfs hfs msdos nfs nfs4 cifs smb3 smbfs") {
		realFS[f] = true
	}
}

var systemPrefixes = []string{"/boot", "/snap", "/var/lib/docker", "/var/lib/containers", "/run/user", "/proc", "/sys", "/dev",
	"/System/Volumes/VM", "/System/Volumes/Preboot", "/System/Volumes/Update", "/System/Volumes/xarts",
	"/System/Volumes/iSCPreboot", "/System/Volumes/Hardware", "/private/var/vm"}

// mountMap is MOUNTS: mount point -> its mount, from /proc/self/mountinfo; nil off Linux.
func mountMap() map[string]mount {
	list := mountinfo()
	if list == nil {
		return nil
	}
	m := map[string]mount{}
	for _, x := range list {
		m[x.path] = x
	}
	return m
}

func rootSource(mounts map[string]mount, root string) string {
	best := ""
	found := false
	for mp := range mounts {
		if (root == mp || strings.HasPrefix(root, strings.TrimRight(mp, "/")+"/")) && (!found || len(mp) > len(best)) {
			best, found = mp, true
		}
	}
	if !found {
		return ""
	}
	return mounts[best].source
}

// Mount is one entry of the "mounts" answer, in agent.py's shape.
type Mount struct {
	Total    int64  `json:"total"`
	Free     int64  `json:"free"`
	Used     int64  `json:"used"`
	Path     string `json:"path"`
	FS       string `json:"fs"`
	Source   string `json:"source"`
	Network  bool   `json:"network"`
	ReadOnly bool   `json:"readonly"`
	Label    string `json:"label"`
}

func hasPrefixAny(s string, prefixes []string) bool {
	for _, p := range prefixes {
		if strings.HasPrefix(s, p) {
			return true
		}
	}
	return false
}

func isDir(p string) bool {
	st, err := os.Stat(p)
	return err == nil && st.IsDir()
}

// listMounts: real filesystems worth scanning, one per device (btrfs subvolumes collapse).
func listMounts() []Mount {
	found := allMounts()
	hostfs := os.Getenv("DC_HOSTFS") // in Docker the host is mounted here; only its mounts are the real drives
	var out []*Mount
	bySource := map[string]*Mount{}
	for _, m := range found {
		inner := m.path
		if hostfs != "" {
			if !(m.path == hostfs || strings.HasPrefix(m.path, hostfs+"/")) {
				continue
			}
			inner = m.path[len(hostfs):]
			if inner == "" {
				inner = "/"
			}
		}
		if !realFS[m.fstype] || hasPrefixAny(inner, systemPrefixes) || !isDir(m.path) {
			continue
		}
		if isDarwin && !(m.path == "/System/Volumes/Data" || strings.HasPrefix(m.path, "/Volumes/")) {
			continue
		}
		u := fsUsage(m.path)
		if u == nil || u.Total < 1<<30 {
			continue
		}
		label := pyBasename(strings.TrimRight(m.path, "/"))
		if label == "" {
			label = m.path // "/" and "C:\\" are their own labels, as in agent.py
		}
		item := &Mount{Total: u.Total, Free: u.Free, Used: u.Used, Path: m.path, FS: m.fstype, Source: m.source,
			Network:  m.fstype == "nfs" || m.fstype == "nfs4" || m.fstype == "cifs" || m.fstype == "smb3" || m.fstype == "smbfs",
			ReadOnly: m.readonly, Label: label}
		prev := bySource[m.source]
		if prev == nil || len(m.path) < len(prev.Path) {
			if prev != nil {
				for i, x := range out {
					if x == prev {
						out = append(out[:i], out[i+1:]...)
						break
					}
				}
			}
			bySource[m.source] = item
			out = append(out, item)
		}
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].Path < out[j].Path })
	res := make([]Mount, len(out))
	for i, m := range out {
		res[i] = *m
	}
	return res
}

// Usage is fs_usage: a filesystem's size, what is free to a user, and what is used.
type Usage struct {
	Total int64 `json:"total"`
	Free  int64 `json:"free"`
	Used  int64 `json:"used"`
}

// pyBasename is os.path.basename: what follows the last separator ('/' everywhere, '\\' too on Windows).
func pyBasename(p string) string {
	i := strings.LastIndex(p, "/")
	if isWindows {
		if j := strings.LastIndexAny(p, `\\:`); j > i {
			i = j
		}
	}
	return p[i+1:]
}
