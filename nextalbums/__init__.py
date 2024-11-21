import sys
from os import path

# !!!!!!!!!!!!!!!!!!!!!!!!
# THIS CAN'T BE INSTALLED AS A REGULAR PACKAGE
# since this acts on data files that I'd rather keep part of this
# package, it has to be installed as an editable package
# like:
# python3 -m pip install install --editable ~/Repos/albums [the root dir with the setup.py file]

this_dir: str = path.dirname(__file__)
root_dir: str = path.abspath(path.join(this_dir, path.pardir))

sys.path.insert(0, root_dir)

import settings as SETTINGS  # noqa
