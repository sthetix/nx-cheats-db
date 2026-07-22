import os
import shutil
import zipfile
import logging
from pathlib import Path
from datetime import date, datetime

import cloudscraper
import rarfile
from bs4 import BeautifulSoup

import process_cheats

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

class DatabaseInfo:
    def __init__(self):
        self.scraper = cloudscraper.create_scraper()
        self.version_url = "https://github.com/HamletDuFromage/switch-cheats-db/releases/latest/download/VERSION"

    def get_database_version(self):
        try:
            response = self.scraper.get(self.version_url, timeout=10)
            response.raise_for_status()
            return date.fromisoformat(response.text.strip())
        except Exception as e:
            logger.error(f"Failed to fetch database version: {e}")
            return date.min

class GbatempCheatsInfo:
    def __init__(self):
        self.scraper = cloudscraper.create_scraper()
        self.github_api_url = "https://api.github.com/repos/tomvita/NXCheatCode/releases/latest"
        self.page_url = "https://gbatemp.net/download/cheat-codes-sxos-and-ams-main-cheat-file-updated.36311/"
        self.github_download_url = None
        self.latest_update_id = None
        self.version = self.fetch_version()

    def fetch_version(self):
        # Try GitHub mirror first
        try:
            token = os.getenv('GITHUB_TOKEN')
            headers = {'Authorization': f'token {token}'} if token else {}
            resp = self.scraper.get(self.github_api_url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                published_at = data.get("published_at")
                if published_at:
                    for asset in data.get("assets", []):
                        if asset.get("name") == "titles.zip":
                            self.github_download_url = asset.get("browser_download_url")
                            break
                    return datetime.fromisoformat(published_at.replace("Z", "+00:00")).date()
        except Exception as e:
            logger.warning(f"GitHub mirror check failed: {e}")

        # Fallback to GBAtemp
        try:
            resp = self.scraper.get(f"{self.page_url}/updates")
            resp.raise_for_status()
            soup = BeautifulSoup(resp.content, "html.parser")

            update_ids = []
            for link in soup.find_all("a", href=True):
                if "/update/" in link['href']:
                    parts = link['href'].split("/update/")
                    if len(parts) > 1:
                        uid = parts[1].strip("/")
                        if uid.isdigit():
                            update_ids.append(int(uid))
            if update_ids:
                self.latest_update_id = max(update_ids)

            block = soup.find("div", {"class": "block-container"})
            if block:
                dates = block.find_all("time", {"class": "u-dt"})
                if dates:
                    return max(datetime.fromisoformat(d.get("datetime")).date() for d in dates)
        except Exception as e:
            logger.error(f"GBAtemp scraping failed: {e}")

        return date.today()

    def has_new_cheats(self, db_version):
        return self.version > db_version

    def get_download_url(self):
        if self.github_download_url:
            return self.github_download_url
        if self.latest_update_id:
            return f"{self.page_url}update/{self.latest_update_id}/download"
        return f"{self.page_url}download"

class HighFPSCheatsInfo:
    def __init__(self):
        self.scraper = cloudscraper.create_scraper()
        self.download_url = "https://github.com/ChanseyIsTheBest/NX-60FPS-RES-GFX-Cheats/archive/refs/heads/main.zip"
        self.api_url = "https://api.github.com/repos/ChanseyIsTheBest/NX-60FPS-RES-GFX-Cheats/branches/main"
        self.version = self.fetch_version()

    def fetch_version(self):
        try:
            token = os.getenv('GITHUB_TOKEN')
            headers = {'Authorization': f'token {token}'} if token else {}
            resp = self.scraper.get(self.api_url, headers=headers)
            resp.raise_for_status()
            last_commit = resp.json().get("commit", {}).get("commit", {}).get("author", {}).get("date")
            return date.fromisoformat(last_commit.split("T")[0])
        except Exception as e:
            logger.error(f"HighFPS version check failed: {e}")
            return date.today()

    def has_new_cheats(self, db_version):
        return self.version > db_version

class ArchiveWorker:
    def __init__(self):
        self.scraper = cloudscraper.create_scraper()

    def download_archive(self, url, dest_path):
        try:
            resp = self.scraper.get(url, allow_redirects=True)
            resp.raise_for_status()
            Path(dest_path).write_bytes(resp.content)
            logger.info(f"Downloaded {url} to {dest_path}")
        except Exception as e:
            logger.error(f"Download failed: {e}")

    def extract_archive(self, archive_path, extract_path):
        extract_path = Path(extract_path)
        extract_path.mkdir(parents=True, exist_ok=True)
        try:
            if rarfile.is_rarfile(str(archive_path)):
                with rarfile.RarFile(str(archive_path)) as rf:
                    rf.extractall(path=str(extract_path))
            elif zipfile.is_zipfile(str(archive_path)):
                with zipfile.ZipFile(str(archive_path)) as zf:
                    zf.extractall(path=str(extract_path))
            else:
                logger.error(f"Unsupported archive format: {archive_path}")
                return False
            logger.info(f"Extracted {archive_path} to {extract_path}")
            return True
        except Exception as e:
            logger.error(f"Extraction failed for {archive_path}: {e}")
            return False

    def build_cheat_files(self, cheats_path, out_path):
        cheats_path = Path(cheats_path)
        titles_path = Path(out_path) / "titles"
        titles_path.mkdir(parents=True, exist_ok=True)

        for json_file in cheats_path.glob("*.json"):
            tid = json_file.stem
            tid_path = titles_path / tid
            tid_path.mkdir(exist_ok=True)

            try:
                with open(json_file, "r") as f:
                    data = json.load(f)

                for key, value in data.items():
                    if key == "attribution":
                        for author, content in value.items():
                            (tid_path / author).write_text(content, encoding="utf-8")
                    else:
                        cheats_folder = tid_path / "cheats"
                        cheats_folder.mkdir(exist_ok=True)
                        if isinstance(value, dict):
                            cheats_content = "".join(value.values())
                            if cheats_content:
                                (cheats_folder / f"{key}.txt").write_text(cheats_content, encoding="utf-8")
            except Exception as e:
                logger.error(f"Error building cheat files for {tid}: {e}")

    def create_archives(self, base_dir, name_prefix):
        base_dir = Path(base_dir)
        titles_dir = base_dir / "titles"
        if not titles_dir.exists():
            return

        # Atmosphere format (contents/)
        contents_dir = base_dir / "contents"
        if contents_dir.exists():
            shutil.rmtree(contents_dir)
        shutil.copytree(titles_dir, contents_dir)

        shutil.make_archive(f"contents_{name_prefix}", "zip", root_dir=base_dir, base_dir="contents")
        shutil.make_archive(f"titles_{name_prefix}", "zip", root_dir=base_dir, base_dir="titles")
        logger.info(f"Created archives for {name_prefix}")

def count_cheats(cheats_dir):
    n_games = 0
    n_updates = 0
    n_cheats = 0
    for json_file in Path(cheats_dir).glob("*.json"):
        try:
            with open(json_file, "r") as f:
                data = json.load(f)
                for key, bid_data in data.items():
                    if key != "attribution":
                        if isinstance(bid_data, dict): n_cheats += len(bid_data)
                        n_updates += 1
            n_games += 1
        except Exception:
            continue

    stats = f"{n_cheats} cheats in {n_games} titles/{n_updates} updates"
    readme = Path("README.md")
    if readme.exists():
        content = readme.read_text()
        if "## Cheats count" in content:
            parts = content.split("## Cheats count")
            content = parts[0] + "## Cheats count\n" + stats + "\n"
        else:
            content += "\n## Cheats count\n" + stats + "\n"
        readme.write_text(content)
    logger.info(f"Database stats: {stats}")

if __name__ == "__main__":
    db = DatabaseInfo()
    db_version = db.get_database_version()
    gbatemp = GbatempCheatsInfo()
    highfps = HighFPSCheatsInfo()

    if gbatemp.has_new_cheats(db_version) or highfps.has_new_cheats(db_version)  :
        worker = ArchiveWorker()

        # GBAtemp
        worker.download_archive(gbatemp.get_download_url(), "gbatemp.zip")
        if worker.extract_archive("gbatemp.zip", "gbatemp_raw"):
            process_cheats.ProcessCheats("gbatemp_raw/titles", "cheats_gbatemp")
            process_cheats.ProcessCheats("gbatemp_raw/titles", "cheats")

        # High FPS
        worker.download_archive(highfps.download_url, "highfps.zip")
        if worker.extract_archive("highfps.zip", "highfps_raw"):
            # Find the actual titles directory inside the extracted zip
            titles_dir = next(Path("highfps_raw").glob("**/titles"), None)
            if titles_dir:
                process_cheats.ProcessCheats(titles_dir, "cheats_gfx")
                process_cheats.ProcessCheats(titles_dir, "cheats")

        # Build complete database
        worker.build_cheat_files("cheats", "complete")

        # Create final archives
        worker.create_archives("complete", "complete")

        # Cleanup
        for p in ["gbatemp.zip", "highfps.zip", "gbatemp_raw", "highfps_raw"]:
            path = Path(p)
            if path.is_file(): path.unlink()
            elif path.is_dir(): shutil.rmtree(path)

        Path("VERSION").write_text(str(date.today()))
        count_cheats("cheats")
    else:
        logger.info("Everything is up to date.")
