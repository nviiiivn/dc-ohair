"""Copies the hub's shared Python (demo data now, the scanner later) into the app.

`flet build` only packages app/src, so the files the app shares with the hub
are copied in here instead of imported from the repo root. Run it before a
build or after changing ../demo.py or ../agent.py; CI runs it too.
"""
import os, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
for name in ('demo.py', 'agent.py'):
    shutil.copyfile(os.path.join(HERE, '..', name), os.path.join(HERE, 'src', 'shared', name))
    print('copied', name)
