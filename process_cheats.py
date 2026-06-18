import json
import logging
import re
import subprocess
from collections import OrderedDict
from pathlib import Path
from string import hexdigits

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

class ProcessCheats:
    def __init__(self, in_path, out_path):
        self.in_path = Path(in_path)
        self.out_path = Path(out_path)
        self.parse_cheats()

    def is_hex_and_16_char(self, name):
        """Checks if a name is a 16-character hexadecimal string."""
        return len(name) == 16 and all(c in hexdigits for c in name)

    def get_cheats_path(self, tid_dir):
        """Returns the 'cheats' subdirectory if it exists."""
        for folder in tid_dir.iterdir():
            if folder.is_dir() and folder.name.lower() == "cheats":
                return folder
        return None

    def get_attribution(self, tid_dir):
        """Reads attribution text files in the title directory."""
        attribution = OrderedDict()
        for f in tid_dir.iterdir():
            if f.is_file() and f.suffix.lower() == ".txt":
                try:
                    attribution[f.name] = f.read_text(encoding="utf-8", errors="ignore")
                except Exception as e:
                    logger.error(f"Failed to read attribution file {f}: {e}")
        return attribution

    def construct_bid_dict(self, sheet_path):
        """Parses a cheat sheet file and returns a dictionary of cheats."""
        out = OrderedDict()
        try:
            content = sheet_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            logger.error(f"Error reading {sheet_path}: {e}")
            return out

        # Match [Cheat Name] or {Cheat Name} and capture the code until the next tag or EOF
        pattern = re.compile(r"(\[.+?\]|\{.+?\})(.*?)(?=(\[.+?\]|\{.+?\}|$))", re.DOTALL)
        for title, code, _ in pattern.findall(content):
            title = title.strip()
            code = code.strip()
            if code and re.search(r"[0-9a-fA-F]{8}", code):
                out[title] = f"{title}\n{code}\n\n"
        return out

    def update_dict(self, new, old):
        """Recursively updates or merges dictionaries."""
        for key, value in new.items():
            if key in old:
                if isinstance(value, dict) and isinstance(old[key], dict):
                    self.update_dict(value, old[key])
                else:
                    old[key] = value
            else:
                old[key] = value
        return old

    def create_json(self, tid_dir):
        """Creates or updates a JSON file for a specific Title ID."""
        cheats_dir = self.get_cheats_path(tid_dir)
        if not cheats_dir:
            return

        out = OrderedDict()
        try:
            for sheet in cheats_dir.iterdir():
                if sheet.is_file() and self.is_hex_and_16_char(sheet.stem):
                    out[sheet.stem.upper()] = self.construct_bid_dict(sheet)
        except Exception as e:
            logger.error(f"Error processing cheats in {cheats_dir}: {e}")

        attribution = self.get_attribution(tid_dir)
        if attribution:
            out["attribution"] = self.update_dict(attribution, out.get("attribution", OrderedDict()))

        if not out:
            return

        cheats_file = self.out_path / f"{tid_dir.name.upper()}.json"
        if cheats_file.exists():
            try:
                with open(cheats_file, "r") as f:
                    existing_data = json.load(f, object_pairs_hook=OrderedDict)
                out = self.update_dict(existing_data, out)
            except Exception as e:
                logger.error(f"Error reading existing JSON {cheats_file}: {e}")

        # Sort the dictionary by keys (Build IDs and attribution)
        sorted_out = OrderedDict(sorted(out.items()))

        try:
            self.out_path.mkdir(parents=True, exist_ok=True)
            with open(cheats_file, "w") as f:
                json.dump(sorted_out, f, indent=4)
            logger.info(f"Updated {cheats_file}")
        except Exception as e:
            logger.error(f"Failed to write {cheats_file}: {e}")

    def parse_cheats(self):
        """Iterates through input directory and processes each Title ID."""
        if not self.in_path.exists():
            logger.error(f"Input path {self.in_path} does not exist.")
            return

        try:
            # Ensure we have permissions (mimic the original chmod but more safely)
            # subprocess.call(['chmod', '-R', '+rw', str(self.in_path)])
            pass
        except:
            pass

        for tid_dir in self.in_path.iterdir():
            if tid_dir.is_dir() and self.is_hex_and_16_char(tid_dir.name):
                self.create_json(tid_dir)

if __name__ == "__main__":
    # This script is usually imported, but added for testing
    import sys
    if len(sys.argv) == 3:
        ProcessCheats(sys.argv[1], sys.argv[2])
