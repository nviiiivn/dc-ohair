package main

// The scan: agent.py's walk, line for line, so the hub cannot tell which of
// the two sent a report. Every folder's totals are complete; files and folders
// of the size cutoff or more are their own nodes, and the small stuff in each
// folder is folded into one "…" node.

import (
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/dlclark/regexp2"
)

const (
	musicI = iota
	gamesI
	seriesI
	moviesI
	otherI
)

// Node is one entry of the size tree, in agent.py's shape.
type Node struct {
	N string   `json:"n"`
	C [5]int64 `json:"c"`
	F int64    `json:"f"`
	T string   `json:"t,omitempty"`
	X int      `json:"x,omitempty"`
	M int64    `json:"m"`
	K []*Node  `json:"k,omitempty"`
}

func (n *Node) size() int64 { return n.C[0] + n.C[1] + n.C[2] + n.C[3] + n.C[4] }

func add(a *[5]int64, b [5]int64) {
	for i := range a {
		a[i] += b[i]
	}
}

// Rules is what decides a file's category: agent.py's DEFAULT_CATEGORIES,
// overridden by the hub's scan.categories.
type Rules struct {
	music, video map[string]bool
	series       *regexp2.Regexp
	games        *regexp2.Regexp
	bigVideo     int64
}

var defaultCategories = map[string]interface{}{
	"music":  []interface{}{".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".alac", ".aiff", ".aif", ".wma", ".ape", ".dsf"},
	"video":  []interface{}{".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".wmv", ".webm", ".mpg", ".mpeg", ".m2ts", ".vob"},
	"series": []interface{}{`[Ss]\d{1,2}[Ee]\d{1,3}`, `season[ ._-]?\d`, "/series/", "/tv/", "/shows/"},
	"games": []interface{}{"/steamapps/", "/steam/", "/games/", "/heroic/", "/lutris/", "gog games", "epic games",
		"/steamlibrary/", "/proton", "/itch/", "/emulat(?!ed/)", "/roms?/"}, // not Android's /storage/emulated/
	"big_video": float64(104857600), // videos smaller than this (100 MiB) count as Other
}

func strList(v interface{}) []string {
	var out []string
	if l, ok := v.([]interface{}); ok {
		for _, x := range l {
			if s, ok := x.(string); ok {
				out = append(out, s)
			}
		}
	}
	return out
}

// truthy is Python's idea of "this setting was given": not missing, not empty, not zero.
func truthy(v interface{}) bool {
	switch x := v.(type) {
	case nil:
		return false
	case []interface{}:
		return len(x) > 0
	case string:
		return x != ""
	case float64:
		return x != 0
	case bool:
		return x
	}
	return true
}

func newRules(custom map[string]interface{}) (*Rules, error) {
	merged := map[string]interface{}{}
	for k, v := range defaultCategories {
		merged[k] = v
	}
	for k, v := range custom {
		if truthy(v) {
			merged[k] = v
		}
	}
	r := &Rules{music: map[string]bool{}, video: map[string]bool{}}
	for _, e := range strList(merged["music"]) {
		r.music[strings.ToLower(e)] = true
	}
	for _, e := range strList(merged["video"]) {
		r.video[strings.ToLower(e)] = true
	}
	var err error
	if r.series, err = regexp2.Compile(strings.Join(strList(merged["series"]), "|"), regexp2.IgnoreCase); err != nil {
		return nil, err
	}
	if r.games, err = regexp2.Compile(strings.Join(strList(merged["games"]), "|"), regexp2.IgnoreCase); err != nil {
		return nil, err
	}
	switch b := merged["big_video"].(type) {
	case float64:
		r.bigVideo = int64(b)
	case string:
		r.bigVideo = 104857600
	}
	return r, nil
}

func search(re *regexp2.Regexp, s string) bool {
	ok, _ := re.MatchString(s)
	return ok
}

// pyExt is Python's os.path.splitext(name)[1]: leading dots are not an extension.
func pyExt(name string) string {
	trimmed := strings.TrimLeft(name, ".")
	i := strings.LastIndex(trimmed, ".")
	if i < 0 {
		return ""
	}
	return trimmed[i:]
}

func (r *Rules) classify(dirpath string, dirGames bool, name string, size int64) int {
	if dirGames || search(r.games, "/"+strings.ToLower(name)) {
		return gamesI
	}
	ext := strings.ToLower(pyExt(name))
	if r.music[ext] {
		return musicI
	}
	if r.video[ext] && size >= r.bigVideo {
		if search(r.series, dirpath+"/"+name) {
			return seriesI
		}
		return moviesI
	}
	return otherI
}

// Scanner holds one run's settings and what it has seen: hard links are counted once across every drive.
type Scanner struct {
	rules  *Rules
	min    int64
	kids   int
	depth  int
	seen   map[[2]uint64]bool
	errors int
	mounts map[string]mount // nil off Linux
}

func (s *Scanner) otherFS(path string, st fileStat, dev uint64, src string) bool {
	if isWindows {
		return false // each drive letter is scanned on its own
	}
	if s.mounts == nil {
		return st.dev != dev
	}
	m, ok := s.mounts[path]
	return ok && m.source != src
}

func excluded(path, name string, patterns []string) bool {
	for _, p := range patterns {
		if path == p || fnmatch(path, p) || fnmatch(name, p) {
			return true
		}
	}
	return false
}

func (s *Scanner) scan(path, name string, dev uint64, src string, exclude []string, depth int) *Node {
	node := &Node{N: name}
	var loose [5]int64
	var looseN, looseM int64
	var kids []*Node
	slashed := strings.ReplaceAll(path, `\`, "/")
	dirGames := search(s.rules.games, strings.ToLower(slashed)+"/")
	f, err := os.Open(path)
	if err != nil {
		s.errors++
		return node
	}
	entries, _ := f.ReadDir(-1) // directory order, as os.scandir gives it
	f.Close()
	for _, e := range entries {
		full := filepath.Join(path, e.Name())
		info, err := e.Info()
		if err != nil {
			s.errors++
			continue
		}
		st := statOf(info)
		if info.IsDir() {
			if e.Name() == ".snapshots" || excluded(full, e.Name(), exclude) || s.otherFS(full, st, dev, src) {
				continue
			}
			kid := s.scan(full, e.Name(), dev, src, exclude, depth+1)
			add(&node.C, kid.C)
			node.F += kid.F
			if kid.M > node.M {
				node.M = kid.M
			}
			kids = append(kids, kid)
		} else if info.Mode().IsRegular() {
			if len(exclude) > 0 && excluded(full, e.Name(), exclude) {
				continue
			}
			if st.nlink > 1 {
				k := [2]uint64{st.dev, st.ino}
				if s.seen[k] {
					continue
				}
				s.seen[k] = true
			}
			size := st.blocks * 512
			if size == 0 {
				size = info.Size()
			}
			ci := s.rules.classify(slashed, dirGames, e.Name(), info.Size())
			node.C[ci] += size
			node.F++
			mtime := info.ModTime().Unix()
			if mtime > node.M {
				node.M = mtime
			}
			if size >= s.min {
				c := [5]int64{}
				c[ci] = size
				kids = append(kids, &Node{N: e.Name(), C: c, F: 1, T: "f", M: mtime})
			} else {
				loose[ci] += size
				looseN++
				if mtime > looseM {
					looseM = mtime
				}
			}
		}
	}
	if depth >= s.depth {
		return node
	}
	sort.SliceStable(kids, func(i, j int) bool { return kids[i].size() > kids[j].size() })
	var keep []*Node
	for _, k := range kids {
		if len(keep) < s.kids && k.size() >= s.min {
			keep = append(keep, k)
		} else {
			add(&loose, k.C)
			looseN += k.F
			if k.M > looseM {
				looseM = k.M
			}
		}
	}
	if len(keep) > 0 {
		if loose[0]+loose[1]+loose[2]+loose[3]+loose[4] > 0 {
			keep = append(keep, &Node{N: "…", C: loose, F: looseN, X: 1, M: looseM})
		}
		node.K = keep
	}
	return node
}
