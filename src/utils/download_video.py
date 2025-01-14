from yt_dlp import YoutubeDL
from tqdm import tqdm
import os

class ProgressBar:
    def __init__(self):
        self.pbar = None

    def __call__(self, d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes', 0) or d.get('total_bytes_estimate', 0)
            downloaded = d.get('downloaded_bytes', 0)

            if self.pbar is None and total:
                self.pbar = tqdm(
                    total=total,
                    unit='iB',
                    unit_scale=True,
                    desc=os.path.basename(d.get('filename', ''))
                )

            if self.pbar:
                self.pbar.n = downloaded
                self.pbar.refresh()

        elif d['status'] == 'finished' and self.pbar:
            self.pbar.close()
            self.pbar = None

def download_video(url, output_path="outputs"):
    # Create output directory if it doesn't exist
    os.makedirs(output_path, exist_ok=True)

    ydl_opts = {
        'format': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]',
        'outtmpl': os.path.join(output_path, '%(id)s.%(ext)s'),
        'progress_hooks': [ProgressBar()],
        'quiet': True,
        'no_warnings': True,
    }
    
    try:
        with YoutubeDL(ydl_opts) as ydl:
            print("Downloading video...")
            ydl.download([url])
            print("Download completed!")
    except Exception as e:
        print(f"An error occurred: {str(e)}")

if __name__ == "__main__":
    video_url = "https://www.tiktok.com/@kindafunkyright/video/7320345381299080481"
    download_video(video_url)