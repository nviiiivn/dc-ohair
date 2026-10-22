//go:build windows

package main

import (
	"os"
	"strings"
	"syscall"
	"unsafe"
)

const isWindows = true

// On Windows, as with Python's os.scandir there, a file's inode and link
// count are not known, so hard links are not deduplicated and the size is
// the file's length.
type fileStat struct {
	dev, ino, nlink uint64
	blocks          int64
}

func statOf(info os.FileInfo) fileStat { return fileStat{} }

func lstatRoot(path string) (fileStat, error) {
	_, err := os.Lstat(path)
	return fileStat{}, err
}

var procGetVolumePathName = syscall.NewLazyDLL("kernel32.dll").NewProc("GetVolumePathNameW")

// isMount is ntpath.ismount: a drive root, or a folder another volume is mounted on.
func isMount(path string) bool {
	p := strings.TrimRight(strings.ReplaceAll(path, "/", `\`), `\`) + `\`
	if len(p) == 3 && p[1] == ':' {
		return true
	}
	in, err := syscall.UTF16PtrFromString(p)
	if err != nil {
		return false
	}
	buf := make([]uint16, 1024)
	r, _, _ := procGetVolumePathName.Call(uintptr(unsafe.Pointer(in)), uintptr(unsafe.Pointer(&buf[0])), uintptr(len(buf)))
	if r == 0 {
		return false
	}
	return strings.EqualFold(syscall.UTF16ToString(buf), p)
}
