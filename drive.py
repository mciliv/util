#!/usr/bin/env python3

import argparse
import io
import hashlib
from pathlib import Path
from typing import Optional, List, Set
from dataclasses import dataclass
from itertools import chain
from functools import partial

import magic
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

import dictionary
import processing
import google_api


def main():
    args = parsed_args()
    drive_folder_path = Path(args.drive_folder)
    if args.list_files:
        Drive.files_in(drive_folder_path)
    else:
        Drive.sync(Path(args.local_path).expanduser(), drive_folder_path, args.only_contents)


def parsed_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('-l', '--local_path', type=str)
    parser.add_argument('-d', '--drive_folder', type=str)
    parser.add_argument('-o', '--only_contents', action='store_true')
    parser.add_argument('-f', '--list-files', action='store_true')
    return parser.parse_args()


class Metadata:
    def __init__(self, fileId: str=None, **kwargs):
        if fileId:
            kwargs |= Metadata.__metadata(fileId)
        for key, value in kwargs.items():
            if value:
                setattr(self, key, value)
            if key == 'parents':
                self.parents = list(self.parents)

    @staticmethod
    def __metadata(file_id: str) -> dict:
        try:
            metadata_dict = Drive.files.get(fileId=file_id, fields='id, name, mimeType, parents').execute()
            return dictionary.replace_key(metadata_dict, 'id', 'fileId')
        except HttpError as e:
            print(f"Failed to fetch metadata for file ID: {file_id}. Error: {e}")

    def query(self, parent_file_id):
        return Query.from_dict(dict(vars(self), parents=parent_file_id))

    def matches_in_parents(self) -> Set[str]:
        return set(chain.from_iterable(map(lambda parent_file_id: self.matches(parent_file_id), self.parents))) if hasattr(self, 'parents') and self.parents else set()

    def matches(self, parent_file_id: str) -> Set[str]:
        return set(map(lambda id_dict: id_dict['id'], Drive.files.list(q=self.query(parent_file_id), fields='files(id)').execute().get('files', [])))


class Query:
    class Clause:
        IN_KEYS = ['parents']

        def __init__(self, key, val, op='='):
            self.key: str = str(key) 
            self.val: str = str(val)
            self.op: str = 'in' if key in Query.Clause.IN_KEYS else str(op)

        def string(self):
            if self.key in Query.Clause.IN_KEYS: 
                return f"'{self.val}' {self.op} {self.key}"
            return f"{self.key} {self.op} '{self.val}'"

    @staticmethod  
    def from_dict(dictionary: dict):
        return Query.query([Query.Clause(key, value) for key, value in dictionary.items()])

    @staticmethod
    def query(clauses: List[Clause], logic_op='and'):
        return f' {logic_op} '.join([clause.string() for clause in clauses])


class File:
    def __init__(self, file_path: Path, mimeType: str=None, file_id: str=None, parent_file_ids: list=None):
        self.file_path = file_path
        self.mimeType = mimeType if mimeType else magic.from_file(str(file_path), mime=True)
        self.metadata = Metadata(fileId=file_id, mimeType=self.mimeType, parents=parent_file_ids, name=self.file_path.name)

    def equivalents(self) -> Set[str]:
        file_ids_of_equivalents = set()
        for file_id in self.metadata.matches_in_parents():
            if Drive.equivalent(self.file_path, file_id):
                file_ids_of_equivalents.add(file_id)
        return file_ids_of_equivalents


class Drive:
    FOLDER_MIMETYPE = 'application/vnd.google-apps.folder'

    service = google_api.service('drive', 3)
    files = service.files()

    @staticmethod
    def sync(file_path: Path, drive_folder: Path, only_contents=False) -> str:
        drive_folder_id = Drive.keep_first(list(Drive.obtain_folders(drive_folder)))
        if file_path.is_dir():
            Drive.sync_subdirs(file_path, drive_folder, only_contents)
            return drive_folder_id
        return Drive.sync_file(file_path, drive_folder_id)

    @staticmethod
    def sync_subdirs(file_path: Path, drive_folder: Path, only_contents):
        sync_parameterized = partial(Drive.sync, drive_folder=drive_folder if only_contents else
        drive_folder / file_path.name, only_contents=only_contents)
        processing.recurse_on_subpaths(sync_parameterized, file_path)

    @staticmethod
    def sync_file(file_path: Path, drive_folder_id: str) -> str:
        file_for_drive = File(file_path, [drive_folder_id])
        equivalent_file_ids = file_for_drive.equivalents()
        for file_id in Metadata(name=file_path.name, parents=file_for_drive.parent_file_ids).matches(drive_folder_id) - equivalent_file_ids: Drive.try_delete(file_id)
        if equivalent_file_ids:
            return Drive.keep_first(list(equivalent_file_ids))
        return Drive.try_write(file_for_drive)

    @staticmethod
    def try_write(file_for_drive: File) -> str:
        try:
            return Drive.write(file_for_drive)            
        except HttpError as error:
            print(f'An error occurred: {error}')

    @staticmethod
    def write(file_for_drive: File) -> str:
        media = MediaFileUpload(str(file_for_drive.file_path),
        mimetype=file_for_drive.mimeType)
        file_id = Drive.files.create(body=vars(file_for_drive.metadata),
                                      media_body=media,
                                      fields='id').execute().get("id")
        return file_id

    @staticmethod
    def obtain_folders(path: Path) -> Optional[Set[str]]:
        path = '/' / Path(path)
        if len(path.parts) == 1:
            return {'root'}
        folder = File(path, Drive.FOLDER_MIMETYPE, parent_file_ids=list(Drive.obtain_folders(path.parent)))
        folder_ids = folder.metadata.matches_in_parents()
        if not folder_ids:
            folder_ids = [Drive.files.create(body=vars(folder.metadata), fields='id').execute()['id']]
        return folder_ids

    @staticmethod
    def print_permissions(folder_id):
        permissions = Drive.service.permissions().list(fileId=folder_id).execute()
        for permission in permissions.get('permissions', []):
            print(f"ID: {permission['id']}, Type: {permission['type']}, Role: {permission['role']}")

    @staticmethod
    def file_content(file_id: str) -> bytes:
        request = Drive.service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        return fh.getvalue()

    @staticmethod
    def equivalent(local_file_path: Path, file_id: str) -> bool:
        google_file_content = Drive.file_content(file_id)
        with open(local_file_path, 'rb') as local_file:
            local_file_content = local_file.read()
        google_file_md5 = hashlib.md5(google_file_content).hexdigest()
        local_file_md5 = hashlib.md5(local_file_content).hexdigest()
        return google_file_md5 == local_file_md5

    @staticmethod
    def keep_first(file_ids: List[str]) -> str:
        for file_id in file_ids[1:]:
            Drive.try_delete(file_id)
        return file_ids[0]
 
    @staticmethod
    def try_delete(file_id: str):
        try:
           Drive.files.delete(fileId=file_id).execute() 
        except HttpError as e:
            print(e)

    @staticmethod
    def files_in(drive_folder_path: Path):
        drive_folder_path = Path(drive_folder_path)
        file_lists = {}
        for folder_id in Drive.obtain_folders(drive_folder_path):
            print(folder_id)
            file_lists[folder_id] = sorted(Drive.list_file_names_in_folder_by_id(folder_id))
            Drive.print_file_names(file_lists[folder_id])
        return file_lists

    @staticmethod
    def files_in_by_id(folder_id: str, fields: str = 'files(id)') -> List[dict]:
        query = f"'{folder_id}' in parents"
        files = []
        page_token = None
        while True:
            try:
                response = Drive.files.list(q=query, pageSize=1000, fields=fields + ',nextPageToken', pageToken=page_token).execute()
                files += response.get('files', [])
                page_token = response.get('nextPageToken', None)
                if page_token is None:
                    break
            except HttpError as e:
                print(f"Error fetching files for folder ID {folder_id}. Error: {e}")
                return []
        return files

    @staticmethod
    def list_file_names_in_folder_by_id(folder_id: str) -> List[str]:
        files = Drive.files_in_by_id(folder_id, 'files(name)')
        return [file['name'] for file in files]

    @staticmethod
    def print_file_names(file_names: List[str]) -> None:
        print("Files in the folder:")
        for name in file_names:
            print(name)
        

if __name__=="__main__":
    main()

