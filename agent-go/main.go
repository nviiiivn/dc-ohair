// Command dc-agent is Disk City's agent as one file with nothing to install:
// the same read-only scan as agent.py, and the push agent's install, run and
// uninstall, for a device with no Python.
//
//	dc-agent install --hub http://hub:7171 --token TOKEN   once
//	dc-agent run                                           what the daily schedule runs
//	dc-agent uninstall
//	dc-agent <base64 spec>                                 exactly what agent.py does with one
//	dc-agent version
package main

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

// Version is set at build time (-ldflags "-X main.Version=0.9.0").
var Version = "dev"

// Spec is what the hub asks for: agent.py's spec.
type Spec struct {
	Mode       string                 `json:"mode"`
	Paths      []string               `json:"paths"`
	Drives     []DriveSpec            `json:"drives"`
	Min        *int64                 `json:"min"`
	Kids       *int                   `json:"kids"`
	Depth      *int                   `json:"depth"`
	Categories map[string]interface{} `json:"categories"`
	Health     string                 `json:"health"`
	Schedule   string                 `json:"schedule"`
}

// DriveSpec is one drive to scan.
type DriveSpec struct {
	Name    string   `json:"name"`
	Path    string   `json:"path"`
	Exclude []string `json:"exclude"`
	Skip    []string `json:"skip"`
	Mounted bool     `json:"mounted"`
}

// Drive is one drive of a report.
type Drive struct {
	Name   string      `json:"name"`
	Path   string      `json:"path"`
	Error  string      `json:"error,omitempty"`
	FS     *Usage      `json:"fs,omitempty"`
	Tree   *Node       `json:"tree,omitempty"`
	Errors *int        `json:"errors,omitempty"`
	Secs   *float64    `json:"secs,omitempty"`
	Disk   *Disk       `json:"disk,omitempty"`
	Health interface{} `json:"health,omitempty"`
}

// Report is agent.py's answer to a scan.
type Report struct {
	Agent   int     `json:"agent"`
	Host    string  `json:"host"`
	Drives  []Drive `json:"drives"`
	Secs    float64 `json:"secs"`
	Scanned string  `json:"scanned"`
	Via     string  `json:"via"`
}

func round1(x float64) float64 { return math.Round(x*10) / 10 }

func hostname() string {
	h, _ := os.Hostname()
	return h
}

func platform() string {
	if runtime.GOOS == "windows" {
		return "win32"
	}
	return runtime.GOOS
}

// Scan runs a scan spec and returns the report agent.py would.
func Scan(spec Spec) (*Report, error) {
	rules, err := newRules(spec.Categories)
	if err != nil {
		return nil, fmt.Errorf("the hub's category rules do not compile: %w", err)
	}
	s := &Scanner{rules: rules, min: 50 << 20, kids: 5000, depth: 12, seen: map[[2]uint64]bool{}, mounts: mountMap()}
	if spec.Min != nil {
		s.min = *spec.Min
	}
	if spec.Kids != nil {
		s.kids = *spec.Kids
	}
	if spec.Depth != nil {
		s.depth = *spec.Depth
	}
	out := &Report{Agent: 3, Host: hostname(), Drives: []Drive{}, Via: "dc-agent " + Version}
	smartCache := map[string]interface{}{}
	t0 := time.Now()
	for _, d := range spec.Drives {
		drive := Drive{Name: d.Name, Path: d.Path}
		root, err := filepath.EvalSymlinks(d.Path)
		if err == nil {
			root, err = filepath.Abs(root)
		}
		var st fileStat
		if err == nil {
			st, err = lstatRoot(root)
		}
		if err != nil {
			drive.Error = "not found: " + strerror(err)
			out.Drives = append(out.Drives, drive)
			continue
		}
		if d.Mounted && !isMount(root) {
			drive.Error = "not mounted"
			out.Drives = append(out.Drives, drive)
			continue
		}
		s.errors = 0
		t1 := time.Now()
		src := ""
		if s.mounts != nil {
			src = rootSource(s.mounts, root)
		}
		exclude := append(append([]string{}, d.Exclude...), d.Skip...)
		tree := s.scan(root, d.Name, st.dev, src, exclude, 0)
		errs, secs := s.errors, round1(time.Since(t1).Seconds())
		drive.FS, drive.Tree, drive.Errors, drive.Secs = fsUsage(root), tree, &errs, &secs
		if disk := diskOf(s.mounts, root); disk != nil {
			drive.Disk = disk
			if spec.Health == "smart" {
				if _, ok := smartCache[disk.Dev]; !ok {
					smartCache[disk.Dev] = smart(disk.Dev)
				}
				drive.Health = smartCache[disk.Dev]
			}
		}
		out.Drives = append(out.Drives, drive)
	}
	out.Secs = round1(time.Since(t0).Seconds())
	out.Scanned = time.Now().Format("2006-01-02T15:04:05-0700")
	return out, nil
}

// strerror is Python's e.strerror: the system's words without Go's "lstat /x: " in front.
func strerror(err error) string {
	if pe, ok := err.(*os.PathError); ok {
		err = pe.Err
	}
	s := err.Error()
	if s == "" {
		return s
	}
	return strings.ToUpper(s[:1]) + s[1:]
}

// Check is the "check" mode: which version is here, and whether each path can be read.
func Check(paths []string) map[string]interface{} {
	list := []map[string]interface{}{}
	for _, p := range paths {
		_, err := os.Stat(p)
		f, rerr := os.Open(p)
		if rerr == nil {
			f.Close()
		}
		list = append(list, map[string]interface{}{"path": p, "exists": err == nil, "readable": rerr == nil})
	}
	return map[string]interface{}{"python": "dc-agent " + Version, "host": hostname(), "platform": platform(), "paths": list}
}

func emit(v interface{}) {
	enc := json.NewEncoder(os.Stdout)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		fatal("could not write the answer: %v", err)
	}
}

func fatal(format string, a ...interface{}) {
	fmt.Fprintf(os.Stderr, "[dc-ohair] "+format+"\n", a...)
	os.Exit(1)
}

func runSpec(arg string) {
	raw, err := base64.StdEncoding.DecodeString(arg)
	if err != nil {
		fatal("that is not a scan spec (base64 JSON): %v", err)
	}
	var spec Spec
	if err := json.Unmarshal(raw, &spec); err != nil {
		fatal("that is not a scan spec: %v", err)
	}
	switch spec.Mode {
	case "mounts":
		emit(map[string]interface{}{"host": hostname(), "mounts": listMounts()})
	case "check":
		emit(Check(spec.Paths))
	default:
		r, err := Scan(spec)
		if err != nil {
			fatal("%v", err)
		}
		emit(r)
	}
}

func main() {
	if len(os.Args) < 2 {
		fmt.Println("Disk City agent", Version, "- usage: dc-agent install --hub URL --token TOKEN | run | uninstall | version")
		os.Exit(2)
	}
	switch os.Args[1] {
	case "version", "--version", "-v":
		fmt.Println("dc-agent", Version, runtime.GOOS+"/"+runtime.GOARCH)
	case "install", "run", "uninstall":
		pushMain(os.Args[1], os.Args[2:])
	default:
		runSpec(os.Args[len(os.Args)-1])
	}
}
