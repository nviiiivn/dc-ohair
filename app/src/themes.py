"""The six themes from the web page (web/themes.css), for the app."""
import flet as ft

THEMES = {
    'dark': dict(bg='#0a0014', bg2='#120025', surface='#1a0033', border='#3a1a5a', text='#d4c8ff', text2='#9a8abb',
                 accent='#ffcc00', green='#00ff88', red='#ff4466', cats=['#f83b8c', '#5df730', '#2bd1fe', '#d9a02c', '#8760eb']),
    'light': dict(bg='#eef5f8', bg2='#eef5f8', surface='#f5eedc', border='#ff0000', text='#2a2218', text2='#6a5a48',
                  accent='#ff0000', green='#6767a3', red='#bb4422', cats=['#89221c', '#019f68', '#204fb5', '#da6600', '#af6af2'], light=True),
    'sepia': dict(bg='#fdf6e3', bg2='#f5e6c8', surface='#efe0b8', border='#d8c8a0', text='#433422', text2='#7a6a5a',
                  accent='#b58900', green='#589500', red='#cb4b16', cats=['#e22cc9', '#16a21c', '#3480fc', '#a82700', '#692e9b'], light=True),
    'cyberpunk': dict(bg='#0a0020', bg2='#120030', surface='#1a0045', border='#6633cc', text='#e0d0ff', text2='#8877bb',
                      accent='#ff00ff', green='#00ffaa', red='#ff0055', cats=['#e70ca9', '#399d57', '#39f2ff', '#fdc010', '#4475f6']),
    'matrix': dict(bg='#000000', bg2='#001100', surface='#002200', border='#003300', text='#00ff00', text2='#006600',
                   accent='#00ff00', green='#00ff00', red='#ff0033', cats=['#f9058e', '#33ae29', '#0bddf3', '#f5cc58', '#9850f2']),
    'ocean': dict(bg='#0a1628', bg2='#0f1f3a', surface='#152a4a', border='#1e3a5f', text='#b0d0ff', text2='#6080b0',
                  accent='#4fc3ff', green='#4caf88', red='#ff5252', cats=['#f12651', '#2afa51', '#31c8fc', '#d3a329', '#b93ef3']),
}
UNSEEN = '#5a5a70'
AMBER = '#f5a524'


def palette(name):
    return THEMES.get(name) or THEMES['dark']


def apply(page, name):
    t = palette(name)
    scheme = ft.ColorScheme(primary=t['accent'], on_primary=t['bg'], secondary=t['accent'], on_secondary=t['bg'],
                            surface=t['bg2'], on_surface=t['text'], on_surface_variant=t['text2'], outline=t['border'],
                            outline_variant=t['border'], error=t['red'], surface_tint=t['bg2'],
                            primary_container=t['surface'], on_primary_container=t['text'],
                            secondary_container=t['surface'], on_secondary_container=t['text'])
    theme = ft.Theme(color_scheme=scheme, use_material3=True)
    page.theme = page.dark_theme = theme
    page.theme_mode = ft.ThemeMode.LIGHT if t.get('light') else ft.ThemeMode.DARK
    page.bgcolor = t['bg']
    return t
