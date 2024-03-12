from pathlib import Path

import arg
from .query import Query
from .api import request, files
from .response import Response
from .file import File, Files


def results():
    return arg.act(parsed_args(), [listing, mapping])


def parsed_args():
    args = arg.parse([
        (('-l', '--local-source-path'), {'type': str}),
        (('-d', '--drive-path'), {'default': '/', 'type': str}),
        (('-f', '--list-files'), {'action': 'store_true'})])
    args.local_source_path = Path(args.local_source_path).expanduser()
    return args


def listing(args):
    if args.list_files: return Files.get(args.drive_path)


def mapping(args):
    if args.local_source_path:
        return File(p=Path(args.local_source_path), parents=\
                    [Files.folder(Path(args.drive_path)).id])
