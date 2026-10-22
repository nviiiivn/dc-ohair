package main

// Python's fnmatch.fnmatch, which drive excludes are written for: '*' and '?'
// match across '/', '[...]' is a set ('[!...]' its complement), and on
// Windows both sides are lower-cased and '/' read as '\' first.

import (
	"regexp"
	"strings"
	"sync"
)

var fnCache sync.Map

func normcase(s string) string {
	if isWindows {
		return strings.ToLower(strings.ReplaceAll(s, "/", `\`))
	}
	return s
}

// translate is fnmatch.translate: a shell pattern as a regular expression for the whole name.
func translate(pat string) string {
	var b strings.Builder
	b.WriteString(`(?s)\A`)
	i, n := 0, len(pat)
	for i < n {
		c := pat[i]
		i++
		switch c {
		case '*':
			for i < n && pat[i] == '*' {
				i++
			}
			b.WriteString(".*")
		case '?':
			b.WriteString(".")
		case '[':
			j := i
			if j < n && pat[j] == '!' {
				j++
			}
			if j < n && pat[j] == ']' {
				j++
			}
			for j < n && pat[j] != ']' {
				j++
			}
			if j >= n {
				b.WriteString(`\[`)
				continue
			}
			stuff := pat[i:j]
			i = j + 1
			stuff = strings.ReplaceAll(stuff, `\`, `\\`)
			if strings.HasPrefix(stuff, "!") {
				stuff = "^" + stuff[1:]
			} else if strings.HasPrefix(stuff, "^") {
				stuff = `\` + stuff
			}
			b.WriteString("[" + stuff + "]")
		default:
			b.WriteString(regexp.QuoteMeta(string(c)))
		}
	}
	b.WriteString(`\z`)
	return b.String()
}

func fnmatch(name, pat string) bool {
	name, pat = normcase(name), normcase(pat)
	v, ok := fnCache.Load(pat)
	if !ok {
		re, err := regexp.Compile(translate(pat))
		if err != nil {
			re = regexp.MustCompile(regexp.QuoteMeta(pat))
		}
		v, _ = fnCache.LoadOrStore(pat, re)
	}
	return v.(*regexp.Regexp).MatchString(name)
}
