package main

// The push agent, as push.py does it, for a device the hub cannot reach:
// install once with the hub's address and this device's token, then a daily
// schedule runs it; each run asks the hub what to scan, scans, and sends the
// report back signed with the token. It keeps its files where push.py does
// (~/.dc-ohair, the token in push.json readable only by you) and uses the
// same schedule name, so it takes over from a push.py install in place.

import (
	"bytes"
	"compress/gzip"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

const label = "dc-ohair-push"
const mark = "# dc-ohair push"

var home = filepath.Join(userHome(), ".dc-ohair")
var confPath = filepath.Join(home, "push.json")

func userHome() string {
	h, err := os.UserHomeDir()
	if err != nil {
		return "."
	}
	return h
}

func selfPath() string {
	name := "dc-agent"
	if isWindows {
		name += ".exe"
	}
	return filepath.Join(home, name)
}

func say(format string, a ...interface{}) { fmt.Printf("[dc-ohair] "+format+"\n", a...) }

// Conf is push.json: the same file push.py writes.
type Conf struct {
	Hub   string `json:"hub"`
	Token string `json:"token"`
}

type hubError struct {
	code int
	body string
}

func (e hubError) Error() string { return fmt.Sprintf("the hub said %d: %s", e.code, e.body) }

var client = &http.Client{Timeout: 120 * time.Second}

func request(conf Conf, path string, body interface{}) ([]byte, error) {
	var rd io.Reader
	method := "GET"
	if body != nil {
		var buf bytes.Buffer
		zw := gzip.NewWriter(&buf)
		if err := json.NewEncoder(zw).Encode(body); err != nil {
			return nil, err
		}
		zw.Close()
		rd, method = &buf, "POST"
	}
	req, err := http.NewRequest(method, strings.TrimRight(conf.Hub, "/")+path, rd)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+conf.Token)
	req.Header.Set("User-Agent", "dc-ohair-push dc-agent/"+Version)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Content-Encoding", "gzip")
	}
	res, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("can't reach the hub: %w", err)
	}
	defer res.Body.Close()
	raw, err := io.ReadAll(res.Body)
	if err != nil {
		return nil, err
	}
	if res.StatusCode >= 400 {
		s := string(raw)
		if len(s) > 200 {
			s = s[:200]
		}
		return nil, hubError{res.StatusCode, s}
	}
	return raw, nil
}

// pushSpec is /api/push/spec: a scan spec whose drives may be the word "auto".
type pushSpec struct {
	Spec
	RawDrives json.RawMessage `json:"drives"`
}

func run(conf Conf) error {
	if err := maybeUpdate(conf); err != nil {
		say("could not update myself (%v); scanning with this version", err)
	}
	raw, err := request(conf, "/api/push/spec", nil)
	if err != nil {
		return err
	}
	var ps pushSpec
	if err := json.Unmarshal(raw, &ps); err != nil {
		return fmt.Errorf("the hub's spec is not JSON: %w", err)
	}
	spec := ps.Spec
	var auto string
	if json.Unmarshal(ps.RawDrives, &auto) == nil && auto == "auto" {
		host := hostname()
		for _, m := range listMounts() {
			root := m.Path == "/" || m.Path == `C:\`
			d := DriveSpec{Name: m.Label, Path: m.Path, Mounted: !root}
			if root {
				d.Name = host
			}
			spec.Drives = append(spec.Drives, d)
		}
	} else if err := json.Unmarshal(ps.RawDrives, &spec.Drives); err != nil && len(ps.RawDrives) > 0 {
		return fmt.Errorf("the hub's drives list is not one I can read: %w", err)
	}
	var paths []string
	for _, d := range spec.Drives {
		paths = append(paths, d.Path)
	}
	if len(paths) == 0 {
		say("scanning nothing (no drives picked yet)")
	} else {
		say("scanning %s", strings.Join(paths, ", "))
	}
	out, err := Scan(spec)
	if err != nil {
		return err
	}
	answer, err := request(conf, "/api/report", out)
	if err != nil {
		return err
	}
	var a struct {
		Msg string `json:"msg"`
	}
	json.Unmarshal(answer, &a)
	if a.Msg == "" {
		a.Msg = "ok"
	}
	say("sent to the hub: %s", a.Msg)
	return nil
}

// maybeUpdate swaps in the hub's build of this agent when the hub has a newer
// one for this system, so hub and device never drift apart (push.py fetches
// the hub's agent.py every run for the same reason). Only the installed copy
// updates itself; the old binary is kept beside it as .old, never deleted.
func maybeUpdate(conf Conf) error {
	self, err := os.Executable()
	if err != nil {
		return err
	}
	if s, err := filepath.EvalSymlinks(self); err == nil {
		self = s
	}
	if !strings.EqualFold(filepath.Clean(self), filepath.Clean(selfPath())) || Version == "dev" {
		return nil
	}
	raw, err := request(conf, "/dl/version", nil)
	if err != nil {
		var he hubError
		if errors.As(err, &he) && he.code == 404 {
			return nil // a hub from before the binary agent: nothing to update from
		}
		return err
	}
	var v struct {
		Version string `json:"version"`
	}
	if json.Unmarshal(raw, &v) != nil || v.Version == "" || v.Version == Version {
		return nil
	}
	bin, err := request(conf, "/dl/"+binaryName(runtime.GOOS, runtime.GOARCH), nil)
	if err != nil {
		return err
	}
	if len(bin) < 1<<20 {
		return fmt.Errorf("the hub sent %d bytes, not an agent", len(bin))
	}
	next := self + ".new"
	if err := os.WriteFile(next, bin, 0o755); err != nil {
		return err
	}
	os.Rename(self, self+".old") // a running program can be renamed, even on Windows
	if err := os.Rename(next, self); err != nil {
		os.Rename(self+".old", self)
		return err
	}
	say("updated myself from %s to %s; the new one runs next time", Version, v.Version)
	return nil
}

// binaryName is the file the hub serves for a system: dc-agent-linux-arm64, dc-agent-windows-amd64.exe, ...
func binaryName(goos, goarch string) string {
	n := "dc-agent-" + goos + "-" + goarch
	if goos == "windows" {
		n += ".exe"
	}
	return n
}

func copySelf() error {
	self, err := os.Executable()
	if err != nil {
		return err
	}
	if strings.EqualFold(filepath.Clean(self), filepath.Clean(selfPath())) {
		return nil
	}
	data, err := os.ReadFile(self)
	if err != nil {
		return err
	}
	tmp := selfPath() + ".new"
	if err := os.WriteFile(tmp, data, 0o755); err != nil {
		return err
	}
	if _, err := os.Stat(selfPath()); err == nil {
		os.Rename(selfPath(), selfPath()+".old") // an earlier install of this agent, kept
	}
	return os.Rename(tmp, selfPath())
}

func install(args []string) error {
	fs := flag.NewFlagSet("install", flag.ExitOnError)
	hub := fs.String("hub", "", "the hub's address, e.g. http://hub:7171")
	token := fs.String("token", "", "this device's enrollment token, from the hub's Settings")
	noSchedule := fs.Bool("no-schedule", false, "do not add the daily schedule")
	fs.Parse(args)
	if *hub == "" || *token == "" {
		return errors.New("install needs --hub and --token (Settings shows the whole command)")
	}
	if err := os.MkdirAll(home, 0o755); err != nil {
		return err
	}
	conf := Conf{Hub: *hub, Token: *token}
	data, _ := json.Marshal(conf)
	if err := os.WriteFile(confPath, data, 0o600); err != nil {
		return err
	}
	os.Chmod(confPath, 0o600)
	if err := copySelf(); err != nil {
		return fmt.Errorf("could not copy myself to %s: %w", selfPath(), err)
	}
	say("first scan, the hub shows it when it arrives")
	if err := run(conf); err != nil {
		return err
	}
	if *noSchedule {
		say(`done; run "%s run" whenever you want a fresh scan`, selfPath())
		return nil
	}
	raw, err := request(conf, "/api/push/spec", nil)
	if err != nil {
		return err
	}
	var spec Spec
	json.Unmarshal(raw, &spec)
	at := spec.Schedule
	if at == "" {
		at = "04:30"
	}
	how, err := scheduleInstall(at)
	if err != nil {
		return fmt.Errorf("the scan worked, but the daily schedule could not be added: %w", err)
	}
	if how == "" {
		say(`done, but there is no scheduler here; run "%s run" yourself`, selfPath())
	} else {
		say("done; it will scan every day at %s via %s", at, how)
	}
	return nil
}

// uninstall takes the schedule away and puts the agent's folder aside, renamed,
// rather than deleting it; the hub still trusts the token until it is revoked in Settings.
func uninstall() error {
	scheduleRemove()
	if _, err := os.Stat(home); err != nil {
		say("removed the schedule; there was no %s", home)
		return nil
	}
	aside := home + "-uninstalled-" + time.Now().Format("20060102-150405")
	if err := os.Rename(home, aside); err != nil {
		return err
	}
	say("removed the schedule and moved %s to %s (delete it whenever you like)", home, aside)
	say("the hub still accepts this device's token until you revoke it in Settings")
	return nil
}

func pushMain(cmd string, args []string) {
	var err error
	switch cmd {
	case "install":
		err = install(args)
	case "uninstall":
		err = uninstall()
	default:
		var raw []byte
		if raw, err = os.ReadFile(confPath); err == nil {
			var conf Conf
			if err = json.Unmarshal(raw, &conf); err == nil {
				err = run(conf)
			}
		} else {
			err = fmt.Errorf("not installed yet (%s is missing): run install first", confPath)
		}
	}
	if err != nil {
		fatal("%v", err)
	}
}
