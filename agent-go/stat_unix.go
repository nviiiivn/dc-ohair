//go:build !windows

package main

import (
	"os"
	"syscall"
)

const isWindows = false

type fileStat struct {
	dev, ino, nlink uint64
	blocks          int64
}

func statOf(info os.FileInfo) fileStat {
	if st, ok := info.Sys().(*syscall.Stat_t); ok {
		return fileStat{dev: uint64(st.Dev), ino: uint64(st.Ino), nlink: uint64(st.Nlink), blocks: int64(st.Blocks)}
	}
	return fileStat{}
}

func lstatRoot(path string) (fileStat, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return fileStat{}, err
	}
	return statOf(info), nil
}

// isMount is posixpath.ismount: another device than its parent, or the same inode as it (/).
func isMount(path string) bool {
	info, err := os.Lstat(path)
	if err != nil || info.Mode()&os.ModeSymlink != 0 {
		return false
	}
	parent, err := os.Lstat(path + "/..")
	if err != nil {
		return false
	}
	a, b := statOf(info), statOf(parent)
	return a.dev != b.dev || a.ino == b.ino
}
