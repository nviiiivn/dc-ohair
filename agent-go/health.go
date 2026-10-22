package main

// Drive health, as agent.py reads it: the whole disk under a drive (Linux),
// and basic SMART from smartctl -a -j when the hub asks for it.

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"time"
)

// Disk is the device under a drive and its /dev/disk/by-id names (serial, WWN).
type Disk struct {
	Dev string   `json:"dev"`
	IDs []string `json:"ids"`
}

func diskOf(mounts map[string]mount, root string) *Disk {
	if len(mounts) == 0 {
		return nil
	}
	src := rootSource(mounts, root)
	if !strings.HasPrefix(src, "/dev/") {
		return nil
	}
	real, err := filepath.EvalSymlinks(src)
	if err != nil {
		real = src
	}
	name := filepath.Base(real)
	sysdir, err := filepath.EvalSymlinks("/sys/class/block/" + name)
	if err == nil {
		if _, err := os.Stat(filepath.Join(sysdir, "partition")); err == nil {
			name = filepath.Base(filepath.Dir(sysdir))
		}
	}
	ids := []string{}
	if entries, err := os.ReadDir("/dev/disk/by-id"); err == nil {
		for _, e := range entries {
			if r, err := filepath.EvalSymlinks("/dev/disk/by-id/" + e.Name()); err == nil && r == "/dev/"+name {
				ids = append(ids, e.Name())
			}
		}
	}
	sort.Strings(ids)
	return &Disk{Dev: "/dev/" + name, IDs: ids}
}

func smartctl() string {
	if p, err := exec.LookPath("smartctl"); err == nil {
		return p
	}
	for _, p := range []string{"/usr/sbin/smartctl", "/sbin/smartctl", "/usr/local/sbin/smartctl", "/opt/homebrew/bin/smartctl"} {
		if _, err := os.Stat(p); err == nil {
			return p
		}
	}
	return ""
}

type obj = map[string]interface{}

func get(m interface{}, keys ...string) interface{} {
	for _, k := range keys {
		mm, ok := m.(obj)
		if !ok {
			return nil
		}
		m = mm[k]
	}
	return m
}

func num(v interface{}) (float64, bool) {
	f, ok := v.(float64)
	return f, ok
}

func summarize(d obj, rc int) obj {
	attrs := map[float64]interface{}{}
	if table, ok := get(d, "ata_smart_attributes", "table").([]interface{}); ok {
		for _, a := range table {
			if id, ok := num(get(a, "id")); ok {
				attrs[id] = get(a, "raw", "value")
			}
		}
	}
	nv, _ := d["nvme_smart_health_information_log"].(obj)
	h := obj{"model": d["model_name"], "serial": d["serial_number"], "temp": get(d, "temperature", "current"),
		"hours": get(d, "power_on_time", "hours"), "realloc": attrs[5], "pending": attrs[197], "uncorrectable": attrs[198],
		"media_errors": get(nv, "media_errors"), "used_pct": get(nv, "percentage_used")}
	notes := []string{}
	var state string
	if passed, ok := get(d, "smart_status", "passed").(bool); (ok && !passed) || rc&8 != 0 {
		state = "failing"
		notes = append(notes, "SMART says the drive is failing")
	} else {
		if rc&16 != 0 {
			notes = append(notes, "an attribute is at its failure threshold")
		}
		if rc&32 != 0 {
			notes = append(notes, "an attribute was past its threshold in the past")
		}
		for _, kl := range [][2]string{{"realloc", "reallocated"}, {"pending", "pending"}, {"uncorrectable", "uncorrectable"}} {
			if v, ok := num(h[kl[0]]); ok && v > 0 {
				notes = append(notes, fmt.Sprintf("%d %s sectors", int64(v), kl[1]))
			}
		}
		if v, ok := num(h["media_errors"]); ok && v > 0 {
			notes = append(notes, fmt.Sprintf("%d media errors", int64(v)))
		}
		if v, ok := num(h["used_pct"]); ok && v >= 90 {
			notes = append(notes, fmt.Sprintf("%d%% of its rated life used", int64(v)))
		}
		if v, ok := num(get(nv, "critical_warning")); ok && v != 0 {
			notes = append(notes, fmt.Sprintf("NVMe critical warning %v", v))
		}
		if v, ok := num(h["temp"]); ok && v >= 60 {
			notes = append(notes, fmt.Sprintf("running hot (%d°C)", int64(v)))
		}
		state = "ok"
		if len(notes) > 0 {
			state = "warning"
		}
	}
	out := obj{"state": state, "notes": notes, "source": "smart"}
	for k, v := range h {
		if v != nil {
			out[k] = v
		}
	}
	return out
}

// smart is basic SMART health for one disk. It needs root, or a sudo rule for smartctl.
func smart(dev string) obj {
	tool := smartctl()
	if tool == "" {
		return obj{"state": "unknown", "note": "smartctl isn't installed here (package smartmontools)"}
	}
	base := []string{tool}
	if runtime.GOOS != "windows" && os.Geteuid() != 0 {
		base = []string{"sudo", "-n", tool}
	}
	last := ""
	for _, extra := range [][]string{{}, {"-d", "sat"}, {"-d", "auto"}} {
		args := append(append(append([]string{}, base[1:]...), "-a", "-j"), extra...)
		cmd := exec.Command(base[0], append(args, dev)...)
		var out, errb bytes.Buffer
		cmd.Stdout, cmd.Stderr = &out, &errb
		done := make(chan error, 1)
		if err := cmd.Start(); err != nil {
			return obj{"state": "unknown", "note": err.Error()}
		}
		go func() { done <- cmd.Wait() }()
		select {
		case <-done:
		case <-time.After(60 * time.Second):
			cmd.Process.Kill()
			return obj{"state": "unknown", "note": "smartctl did not answer within a minute"}
		}
		if bytes.Contains(errb.Bytes(), []byte("password is required")) || bytes.Contains(errb.Bytes(), []byte("a terminal is required")) {
			return obj{"state": "unknown", "note": "smartctl needs root: add a sudo rule for it (see docs/security.md)"}
		}
		var d obj
		if json.Unmarshal(out.Bytes(), &d) != nil {
			s := strings.TrimSpace(errb.String())
			if len(s) > 200 {
				s = s[len(s)-200:]
			}
			last = s
			continue
		}
		if d["smart_status"] != nil || d["nvme_smart_health_information_log"] != nil || d["ata_smart_attributes"] != nil {
			rc := 0
			if cmd.ProcessState != nil {
				rc = cmd.ProcessState.ExitCode()
			}
			return summarize(d, rc)
		}
		if msgs, ok := get(d, "smartctl", "messages").([]interface{}); ok && len(msgs) > 0 {
			if s, ok := get(msgs[0], "string").(string); ok {
				last = s
			}
		}
	}
	if last == "" {
		last = "no SMART data (USB enclosures sometimes hide it)"
	}
	return obj{"state": "unknown", "note": last}
}
