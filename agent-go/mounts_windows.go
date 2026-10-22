//go:build windows

package main

import (
	"os"
	"syscall"
	"unsafe"
)

const isDarwin = false

func mountinfo() []mount { return nil }

// allMounts: every drive letter that exists, as agent.py lists them on Windows.
func allMounts() []mount {
	var found []mount
	for c := 'C'; c <= 'Z'; c++ {
		root := string(c) + `:\`
		if _, err := os.Stat(root); err == nil {
			found = append(found, mount{path: root, source: string(c) + ":", fstype: "ntfs"})
		}
	}
	return found
}

var procGetDiskFreeSpaceEx = syscall.NewLazyDLL("kernel32.dll").NewProc("GetDiskFreeSpaceExW")

// fsUsage is shutil.disk_usage on Windows: free is all the free space, used is the rest.
func fsUsage(path string) *Usage {
	p, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		return nil
	}
	var avail, total, free uint64
	r, _, _ := procGetDiskFreeSpaceEx.Call(uintptr(unsafe.Pointer(p)), uintptr(unsafe.Pointer(&avail)),
		uintptr(unsafe.Pointer(&total)), uintptr(unsafe.Pointer(&free)))
	if r == 0 {
		return nil
	}
	return &Usage{Total: int64(total), Free: int64(free), Used: int64(total - free)}
}
