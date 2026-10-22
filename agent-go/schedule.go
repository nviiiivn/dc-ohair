package main

// The daily schedule, the same four kinds push.py makes and under the same
// name: Task Scheduler on Windows, launchd on macOS, a systemd user timer on
// Linux when there is one, else cron (LibreELEC, NAS boxes, root).

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
)

func parseAt(at string) (int, int, error) {
	var h, m int
	if _, err := fmt.Sscanf(at, "%d:%d", &h, &m); err != nil || h < 0 || h > 23 || m < 0 || m > 59 {
		return 0, 0, fmt.Errorf("bad time %q", at)
	}
	return h, m, nil
}

func plistPath() string {
	return filepath.Join(userHome(), "Library", "LaunchAgents", label+".plist")
}

func unitDir() string {
	return filepath.Join(userHome(), ".config", "systemd", "user")
}

func have(name string) bool {
	_, err := exec.LookPath(name)
	return err == nil
}

func systemdUser() bool {
	if !have("systemctl") || os.Geteuid() == 0 {
		return false
	}
	out, _ := exec.Command("systemctl", "--user", "is-system-running").Output()
	s := strings.TrimSpace(string(out))
	return s == "running" || s == "degraded"
}

func cronLines() []string {
	out, _ := exec.Command("crontab", "-l").Output()
	var keep []string
	for _, l := range strings.Split(strings.TrimRight(string(out), "\n"), "\n") {
		if l != "" && !strings.Contains(l, mark) {
			keep = append(keep, l)
		}
	}
	return keep
}

func setCron(lines []string) error {
	cmd := exec.Command("crontab", "-")
	cmd.Stdin = strings.NewReader(strings.Join(lines, "\n") + "\n")
	return cmd.Run()
}

func scheduleInstall(at string) (string, error) {
	h, m, err := parseAt(at)
	if err != nil {
		return "", err
	}
	self := selfPath()
	switch {
	case runtime.GOOS == "windows":
		tr := fmt.Sprintf(`"%s" run`, self)
		out, err := exec.Command("schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", fmt.Sprintf("%02d:%02d", h, m), "/TN", label, "/TR", tr).CombinedOutput()
		if err != nil {
			return "", fmt.Errorf("%v: %s", err, strings.TrimSpace(string(out)))
		}
		return "Task Scheduler (" + label + ")", nil
	case runtime.GOOS == "darwin":
		p := plistPath()
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			return "", err
		}
		plist := fmt.Sprintf(`<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>%s</string>
  <key>ProgramArguments</key><array><string>/usr/bin/caffeinate</string><string>-i</string><string>%s</string><string>run</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>%d</integer><key>Minute</key><integer>%d</integer></dict>
</dict></plist>
`, label, xmlEscape(self), h, m)
		if err := os.WriteFile(p, []byte(plist), 0o644); err != nil {
			return "", err
		}
		exec.Command("launchctl", "unload", p).Run()
		if out, err := exec.Command("launchctl", "load", p).CombinedOutput(); err != nil {
			return "", fmt.Errorf("%v: %s", err, strings.TrimSpace(string(out)))
		}
		return "launchd (" + p + ")", nil
	case systemdUser():
		dir := unitDir()
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return "", err
		}
		service := fmt.Sprintf("[Unit]\nDescription=Disk City push agent\n\n[Service]\nType=oneshot\nExecStart=%s run\n", self)
		timer := fmt.Sprintf("[Unit]\nDescription=Disk City daily push\n\n[Timer]\nOnCalendar=*-*-* %02d:%02d:00\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n", h, m)
		if err := os.WriteFile(filepath.Join(dir, label+".service"), []byte(service), 0o644); err != nil {
			return "", err
		}
		if err := os.WriteFile(filepath.Join(dir, label+".timer"), []byte(timer), 0o644); err != nil {
			return "", err
		}
		if err := exec.Command("systemctl", "--user", "daemon-reload").Run(); err != nil {
			return "", err
		}
		if out, err := exec.Command("systemctl", "--user", "enable", "--now", label+".timer").CombinedOutput(); err != nil {
			return "", fmt.Errorf("%v: %s", err, strings.TrimSpace(string(out)))
		}
		return `a systemd user timer (run "loginctl enable-linger" so it also runs while you are logged out)`, nil
	case have("crontab"):
		lines := append(cronLines(), fmt.Sprintf("%d %d * * * %s run >/dev/null 2>&1 %s", m, h, shellQuote(self), mark))
		if err := setCron(lines); err != nil {
			return "", err
		}
		return "cron", nil
	}
	return "", nil
}

func scheduleRemove() {
	switch runtime.GOOS {
	case "windows":
		exec.Command("schtasks", "/Delete", "/F", "/TN", label).Run()
		return
	case "darwin":
		p := plistPath()
		exec.Command("launchctl", "unload", p).Run()
		if _, err := os.Stat(p); err == nil {
			os.Rename(p, p+".removed") // launchd ignores it under that name
		}
		return
	}
	timer := filepath.Join(unitDir(), label+".timer")
	if _, err := os.Stat(timer); err == nil {
		exec.Command("systemctl", "--user", "disable", "--now", label+".timer").Run()
		os.Rename(timer, timer+".removed")
		os.Rename(filepath.Join(unitDir(), label+".service"), filepath.Join(unitDir(), label+".service.removed"))
		exec.Command("systemctl", "--user", "daemon-reload").Run()
	}
	if have("crontab") {
		out, _ := exec.Command("crontab", "-l").Output()
		if strings.Contains(string(out), mark) {
			setCron(cronLines())
		}
	}
}

func xmlEscape(s string) string {
	return strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;").Replace(s)
}

func shellQuote(s string) string {
	if !strings.ContainsAny(s, " \t'\"$\\`") {
		return s
	}
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}
