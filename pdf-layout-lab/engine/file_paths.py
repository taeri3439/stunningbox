"""Filesystem paths for Python IO, including Windows paths longer than MAX_PATH."""
import os
from pathlib import Path


def io_path(path):
    path = Path(path)
    if os.name != 'nt':
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith('\\\\?\\'):
        return Path(absolute)
    if absolute.startswith('\\\\'):
        return Path('\\\\?\\UNC\\' + absolute[2:])
    return Path('\\\\?\\' + absolute)
