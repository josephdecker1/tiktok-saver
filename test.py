import platform
from pathlib import Path
from shutil import rmtree
from time import sleep

txt_files = list(Path(Path.home() / "Downloads" / "TikTok").glob("*.txt"))

for x in txt_files:
    print(x.name)
    if ' ' in x.name or '/' in x.name:
        print(f"found space in {x.name}")
        x.rename(x.with_name(x.name.replace(' ', '-').replace("/", "-")))
        print(f"renamed to {x.name}")
    
    # Collection folders
    col = Path(Path.home() / "Downloads" / "TikTok" / x.name.split('.')[0])
    if not col.exists():
        col.mkdir()

    with open(x, 'r', encoding='utf-8') as f:
        for line in f:
            print(line)