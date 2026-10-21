"""Copies what the app shares with the hub into it: the demo data and the
scanner (into src/shared) and the 3D city page (into src/web).

`flet build` only packages app/src, so these are copied in here instead of
imported from the repo root. Run it before a build or after changing
../demo.py, ../agent.py or ../web; CI runs it too.
"""
import os, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
for name in ('demo.py', 'agent.py'):
    shutil.copyfile(os.path.join(HERE, '..', name), os.path.join(HERE, 'src', 'shared', name))
    print('copied', name)
web = os.path.join(HERE, 'src', 'web')
shutil.copytree(os.path.join(HERE, '..', 'web'), web, dirs_exist_ok=True)
print('copied web/')
