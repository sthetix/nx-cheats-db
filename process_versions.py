#!/usr/bin/env python3

import json
import requests
import os

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


class ProcessVersions:
    def __init__(self, cnmts_url, titles_url, versions_url):
        self.json_path = "versions.json"
        self.dir_path = "versions/"
        self.changed = False
        self.versions_dict = dict()
        self.data = dict()
        try:
            self.data = self.merge_cmts_and_versions(cnmts_url, versions_url)
        except ValueError:
            print("Invalid JSON file!")
        self.title_dict = self.create_names_dict(titles_url)

    def merge_cmts_and_versions(self, cnmts_url, versions_url):
        cnmt_resp = requests.get(cnmts_url, headers=HEADERS)
        ver_resp = requests.get(versions_url, headers=HEADERS)
        cmnts = json.loads(cnmt_resp.text)
        versions = json.loads(ver_resp.text)
        for tid, value in versions.items():
            cmnts[tid] = {**value, **cmnts.get(tid, {})}
        return cmnts

    def update_versions(self):
        if self.data:
            self.get_version_dict()
            self.check_for_changes()
            self.write_master_files()
            self.write_title_files()

    def get_version_dict(self):
        for tid in self.data:
            tid_base = tid[:13].upper() + "000"
            if (tid_base) not in self.versions_dict:
                self.versions_dict[tid_base] = {}
                try:
                    self.versions_dict[tid_base]["title"] = self.title_dict[tid_base]
                except KeyError:
                    pass

            latest_ver = 0
            for ver in self.data[tid]:
                try:
                    if "buildId" in self.data[tid][ver]["contentEntries"][0]:
                        self.versions_dict[tid_base][str(self.data[tid][ver]["version"])
                                                                    ] = self.data[tid][ver]["contentEntries"][0]["buildId"][:16].upper()
                except:
                    pass
                latest_ver = max(latest_ver, int(ver))
            self.versions_dict[tid_base]["latest"] = latest_ver

    def check_for_changes(self):
        try:
            with open(self.json_path, 'r') as read_file:
                old = json.load(read_file)
            if old != self.versions_dict:
                self.changed = True
                print(f"{self.json_path} changed")
        except FileNotFoundError:
            print("File doesn't exist")
            self.changed = True

    def write_master_files(self):
        with open(self.json_path, 'w') as json_file:
            json.dump(self.versions_dict, json_file, indent=4, sort_keys=True)

    def write_title_files(self):
        if not(os.path.exists(self.dir_path)):
            os.mkdir(self.dir_path)

        for tid in self.versions_dict:
            path = f"{self.dir_path}{tid}.json"
            with open(path, 'w') as json_file:
                json.dump(
                    self.versions_dict[tid], json_file, indent=4, sort_keys=True)

    def create_names_dict(self, url):
        out = dict()
        resp = requests.get(url, headers=HEADERS)
        try:
            data = json.loads(resp.text)
        except json.JSONDecodeError as e:
            print(f"JSON decode error from {url}: {e}")
            print(f"Response status: {resp.status_code}")
            print(f"Response (first 500 chars): {resp.text[:500]}")
            raise
        for key, value in data.items():
            out[value["id"]] = value["name"]
        return out


if __name__ == '__main__':
    processor = ProcessVersions(
        "https://raw.githubusercontent.com/blawar/titledb/master/cnmts.json",
        "https://raw.githubusercontent.com/blawar/titledb/master/US.en.json",
        "https://raw.githubusercontent.com/blawar/titledb/master/versions.json"
    )
    processor.update_versions()
