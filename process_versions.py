import json
import logging
import re
from pathlib import Path

import requests

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

class ProcessVersions:
    def __init__(self, cnmts_url, titles_url, versions_url):
        self.json_path = Path("versions.json")
        self.dir_path = Path("versions/")
        self.changed = False
        self.versions_dict = {}

        try:
            self.data = self.merge_cnmts_and_versions(cnmts_url, versions_url)
            self.title_dict = self.create_names_dict(titles_url)
        except Exception as e:
            logger.error(f"Initialization failed: {e}")
            self.data = {}
            self.title_dict = {}

    def merge_cnmts_and_versions(self, cnmts_url, versions_url):
        try:
            cnmt_resp = requests.get(cnmts_url, headers=HEADERS)
            ver_resp = requests.get(versions_url, headers=HEADERS)
            cnmt_resp.raise_for_status()
            ver_resp.raise_for_status()

            cnmts = cnmt_resp.json()
            versions = ver_resp.json()

            for tid, value in versions.items():
                if tid in cnmts:
                    cnmts[tid].update(value)
                else:
                    cnmts[tid] = value
            return cnmts
        except Exception as e:
            logger.error(f"Failed to merge CNMTs and versions: {e}")
            return {}

    def is_switch2_title(self, title):
        keywords = ("Nintendo Switch 2", "Nintendo Switch\u2122 2")
        return any(kw in title for kw in keywords)

    def get_version_dict(self):
        cheats_dir = Path("cheats")
        for tid, versions in self.data.items():
            tid_base = tid[:13].upper() + "000"
            if tid_base not in self.versions_dict:
                title = self.title_dict.get(tid_base, "")
                has_cheats = (cheats_dir / f"{tid_base}.json").exists()

                if title and self.is_switch2_title(title) and not has_cheats:
                    continue

                self.versions_dict[tid_base] = {}
                if title:
                    clean_title = re.sub(r"[\s\-–:]+Nintendo Switch[\u2122]? 2.*$", "", title).strip()
                    self.versions_dict[tid_base]["title"] = clean_title or title

            latest_ver = 0
            for ver_num, ver_data in versions.items():
                try:
                    if "contentEntries" in ver_data and ver_data["contentEntries"]:
                        bid = ver_data["contentEntries"][0].get("buildId")
                        if bid:
                            self.versions_dict[tid_base][str(ver_num)] = bid[:16].upper()
                    latest_ver = max(latest_ver, int(ver_num))
                except:
                    continue
            self.versions_dict[tid_base]["latest"] = latest_ver

    def update_versions(self):
        if not self.data: return

        self.get_version_dict()

        # Check for changes
        if self.json_path.exists():
            try:
                with open(self.json_path, "r") as f:
                    old_data = json.load(f)
                if old_data != self.versions_dict:
                    self.changed = True
                    logger.info(f"{self.json_path} changed")
            except:
                self.changed = True
        else:
            self.changed = True

        # Write files
        try:
            if self.changed:
                with open(self.json_path, "w") as f:
                    json.dump(self.versions_dict, f, indent=4, sort_keys=True)
                logger.info(f"Updated {self.json_path}")

            self.dir_path.mkdir(exist_ok=True)
            for tid, data in self.versions_dict.items():
                file_path = self.dir_path / f"{tid}.json"
                new_content = json.dumps(data, indent=4, sort_keys=True)

                # Only write if file doesn't exist or content has changed
                if file_path.exists() and file_path.read_text() == new_content:
                    continue

                with open(file_path, "w") as f:
                    f.write(new_content)
        except Exception as e:
            logger.error(f"Failed to write version files: {e}")

    def create_names_dict(self, url):
        try:
            resp = requests.get(url, headers=HEADERS)
            resp.raise_for_status()
            data = resp.json()
            return {v["id"]: v["name"] for v in data.values() if "id" in v and "name" in v}
        except Exception as e:
            logger.error(f"Failed to create names dict: {e}")
            return {}

if __name__ == '__main__':
    processor = ProcessVersions(
        "https://raw.githubusercontent.com/blawar/titledb/master/cnmts.json",
        "https://raw.githubusercontent.com/blawar/titledb/master/US.en.json",
        "https://raw.githubusercontent.com/blawar/titledb/master/versions.json"
    )
    processor.update_versions()
