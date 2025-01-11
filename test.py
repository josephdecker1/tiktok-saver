import platform
from pathlib import Path

print(platform.system())
# print(Path.home() / "Downloads")
# print(Path(__file__).resolve().parent / "src" / "collection_links_download.js")


# data_dir = Path(__file__).resolve().parent / "data"
# if not data_dir.exists():
#     data_dir.mkdir()

txt_files = list(Path(Path.home() / "Downloads" / "TikTok").glob("*.txt"))

for x in txt_files:
    print(x)