"""What a video file is, from its name: the title and year (or the show and
episode), and how good a copy it is.

Release names follow one loose convention, the one Plex, Jellyfin and Radarr
read: "Title (Year)" or "Title.Year.2160p.BluRay.x265-GROUP", with the
quality tags after the year. Nothing here opens a file; the name and the
folder it sits in are all there is.

Quality is ranked the way Radarr ranks it: resolution first, then source
(Remux > BluRay > WEB-DL > WEBRip > HDTV > DVD > the cam rips), then HDR,
then codec. Between two copies that tie on all of those, the bigger file
has the higher bitrate and wins.
"""
import os, re

VIDEO = {'.mkv', '.mp4', '.avi', '.m4v', '.mov', '.ts', '.wmv', '.webm', '.mpg', '.mpeg', '.m2ts', '.vob'}

RESOLUTIONS = [  # (rank, label, pattern)
    (4, '2160p', r'2160p|4k|uhd'),
    (3, '1080p', r'1080[pi]'),
    (2, '720p', r'720p'),
    (1, '480p', r'480p|576p|sd'),
]
SOURCES = [
    (7, 'Remux', r'remux'),
    (6, 'BluRay', r'blu-?ray|bdrip|brrip|bd25|bd50'),
    (5, 'WEB-DL', r'web-?dl|webdl|amzn|nf|dsnp|hmax|atvp'),
    (4, 'WEBRip', r'web-?rip|web'),
    (3, 'HDTV', r'hdtv|pdtv|dsr'),
    (2, 'DVD', r'dvd-?rip|dvd(?:5|9)?|dvdscr'),
    (0, 'CAM', r'cam-?rip|cam|hdcam|telesync|ts-?rip|hdts|tc|telecine'),
]
HDRS = [(2, 'Dolby Vision', r'dv|dovi|dolby[ .-]?vision'), (1, 'HDR', r'hdr10\+?|hdr')]
CODECS = [(3, 'AV1', r'av1'), (2, 'HEVC', r'x265|h\.?265|hevc'), (1, 'AVC', r'x264|h\.?264|avc')]
AUDIOS = [(5, 'Atmos', r'atmos'), (4, 'TrueHD', r'truehd'), (4, 'DTS-HD MA', r'dts-?hd(?:[ .-]?ma)?|dts-?x'),
          (3, 'DTS', r'dts'), (2, 'DD+', r'ddp|dd\+|e-?ac-?3'), (1, 'DD', r'ac-?3|dd5|dd2'), (1, 'AAC', r'aac')]
EDITIONS = r"extended|director'?s[ .]cut|unrated|uncut|theatrical|remastered|imax|criterion|special[ .]edition|final[ .]cut"

YEAR = re.compile(r'(?<![0-9])(19[2-9][0-9]|20[0-4][0-9])(?![0-9])')
EPISODE = re.compile(r'[Ss](\d{1,2})[ ._-]?[Ee](\d{1,3})|(?<!\d)(\d{1,2})x(\d{2,3})(?!\d)')
SAMPLE = re.compile(r'(^|[ ._-])sample([ ._-]|$)|^sample', re.I)
TAG_SPLIT = re.compile(r'[ ._\[\]()-]+')


def _first(table, text):
    for rank, label, pattern in table:
        if re.search(r'(?<![a-z0-9])(?:%s)(?![a-z0-9])' % pattern, text):
            return rank, label
    return 0, ''


def _words(text):
    """Dots and underscores to spaces, brackets dropped, case kept."""
    text = re.sub(r'[._]+', ' ', text)
    text = re.sub(r'[\[\]{}]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip(' -')


def normal_title(title):
    """The key two copies of a film share: lower case, no punctuation, '&' as 'and', no leading 'the'."""
    t = title.lower().replace('&', ' and ')
    t = re.sub(r"['’]", '', t)
    t = re.sub(r'[^a-z0-9]+', ' ', t).strip()
    return re.sub(r'^(the|a|an) ', '', t)


def parse(path):
    """A dict for a video at [path], or None for anything that isn't one.

    kind is 'movie' (title and year), 'episode' (show, season, episode) or
    'video' (neither could be read). The folder name stands in for a file name
    that has lost its title ("movie.mkv" inside "Heat (1995)").
    """
    name = os.path.basename(path)
    stem, ext = os.path.splitext(name)
    if ext.lower() not in VIDEO:
        return None
    folder = os.path.basename(os.path.dirname(path))
    low = (folder + ' ' + stem).lower()
    info = {'name': name, 'sample': bool(SAMPLE.search(stem) or SAMPLE.search(folder)),
            'edition': ''}
    info['res_rank'], info['resolution'] = _first(RESOLUTIONS, low)
    info['src_rank'], info['source'] = _first(SOURCES, low)
    info['hdr_rank'], info['hdr'] = _first(HDRS, low)
    info['codec_rank'], info['codec'] = _first(CODECS, low)
    info['audio_rank'], info['audio'] = _first(AUDIOS, low)
    m = re.search(r'(?<![a-z])(%s)(?![a-z])' % EDITIONS, low)
    if m:
        info['edition'] = _words(m.group(1)).title()
    if info['src_rank'] == 7 and not info['res_rank']:
        info['res_rank'], info['resolution'] = 3, '1080p'    # a Remux with no resolution tag is a Blu-ray's 1080p

    ep = EPISODE.search(stem) or EPISODE.search(folder)
    if ep:
        season, episode = (ep.group(1), ep.group(2)) if ep.group(1) else (ep.group(3), ep.group(4))
        src = stem if EPISODE.search(stem) else folder
        show = _words(src[:EPISODE.search(src).start()])
        show = YEAR.sub('', show).strip(' -()') or _words(os.path.basename(os.path.dirname(os.path.dirname(path))))
        show = re.sub(r'(?i)\bseason \d+\b', '', show).strip(' -')
        info.update(kind='episode', title=show, season=int(season), episode=int(episode),
                    key='episode:%s:s%02de%02d' % (normal_title(show), int(season), int(episode)))
        return info

    for src in (stem, folder):
        years = list(YEAR.finditer(src))
        # The LAST plausible year is the release year: "2001 A Space Odyssey (1968)".
        y = next((m for m in reversed(years) if m.start() > 0), None)
        if y:
            title = _words(src[:y.start()]).strip(' -(')
            if title:
                info.update(kind='movie', title=title, year=int(y.group(1)),
                            key='movie:%s:%s' % (normal_title(title), y.group(1)))
                return info
    title = _words(re.split(r'(?i)[ ._](?:2160p|1080p|720p|480p|bluray|web|hdtv|x26[45])', stem)[0])
    info.update(kind='video', title=title, key='')
    return info


def guess_resolution(size, kind='movie'):
    """A resolution rank from size alone, for a name with no quality tags: a
    two-hour film is rarely under 4 GB at 1080p or 15 GB at 2160p (an episode
    is about a quarter of that). Only for ranking; the label still says unknown."""
    gb = size / 1e9 * (4 if kind == 'episode' else 1)
    return 4 if gb >= 15 else 3 if gb >= 4 else 2 if gb >= 1.5 else 1 if size else 0


def score(info, size=0):
    """A sortable quality: higher is the better copy. A name with no
    resolution is ranked by its size's likely one, so an untagged 20 GB copy is
    not beaten by a tagged 2 GB 720p."""
    res = info['res_rank'] or guess_resolution(size, info.get('kind'))
    return (res, info['src_rank'], info['hdr_rank'], info['codec_rank'], info['audio_rank'], size)


def label(info):
    """'2160p BluRay HDR HEVC Atmos', the parts that are known."""
    return ' '.join(p for p in (info['resolution'], info['source'], info['hdr'], info['codec'], info['audio'],
                                info['edition']) if p) or 'unknown quality'
