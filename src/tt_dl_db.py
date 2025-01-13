from yt_dlp import YoutubeDL
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
import math
import argparse
from pathlib import Path
import sqlite3
from datetime import datetime
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

def init_database(db_user='user'):
    """Create the database and tables if they don't exist"""
    conn = sqlite3.connect(f'tt_metadata_{db_user}.db')
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS videos (
            video_id TEXT PRIMARY KEY,
            title TEXT,
            channel TEXT,
            duration INTEGER,
            view_count INTEGER,
            like_count INTEGER,
            upload_date TEXT,
            description TEXT,
            download_date TEXT,
            url TEXT,
            filepath TEXT
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS formats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT,
            format_id TEXT,
            resolution TEXT,
            filesize INTEGER,
            FOREIGN KEY (video_id) REFERENCES videos (video_id)
        )
    ''')
    
    conn.commit()
    return conn

def download_and_store_video(url, output_path="outputs", db_user='user'):
    # os.makedirs(output_path, exist_ok=True)
    conn = init_database(db_user)
    c = conn.cursor()

    try:
        with YoutubeDL() as ydl:
            # First get info without downloading
            print("Extracting video information...")
            info = ydl.extract_info(url, download=False)
            
            # Determine output filename
            video_id = info.get('id')
            video_ext = info['ext'] if 'ext' in info else 'mp4'
            output_filename = f"{video_id}.{video_ext}"
            output_filepath = os.path.join(output_path, output_filename)
            
            # Configure download options with known filename
            ydl_opts = {
                'format': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]',
                'outtmpl': output_filepath,
                'progress_hooks': [ProgressBar()],
                'quiet': True,
                'no_warnings': True,
            }
            
            # Store metadata in database with filepath
            video_data = {
                'video_id': video_id,
                'title': info.get('title'),
                'channel': info.get('channel'),
                'duration': info.get('duration'),
                'view_count': info.get('view_count'),
                'like_count': info.get('like_count'),
                'upload_date': info.get('upload_date'),
                'description': info.get('description'),
                'download_date': datetime.now().isoformat(),
                'url': url,
                'filepath': output_filepath
            }
            
            c.execute('''
                INSERT OR REPLACE INTO videos 
                (video_id, title, channel, duration, view_count, like_count, 
                upload_date, description, download_date, url, filepath)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', tuple(video_data.values()))
            
            # Store format information
            c.execute('DELETE FROM formats WHERE video_id = ?', (video_id,))
            
            for fmt in info['formats']:
                format_data = (
                    video_id,
                    fmt.get('format_id'),
                    fmt.get('resolution', 'N/A'),
                    fmt.get('filesize', 0)
                )
                c.execute('''
                    INSERT INTO formats (video_id, format_id, resolution, filesize)
                    VALUES (?, ?, ?, ?)
                ''', format_data)
            
            conn.commit()
            print(f"Stored metadata for video: {info.get('title')}")
            
            # Download the video
            print("Downloading video...")
            with YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            print("Download completed!")
            
            # Verify stored data and filepath
            print("\nStored Data:")
            c.execute('''
                SELECT v.title, v.channel, v.view_count, v.filepath, COUNT(f.id) as format_count 
                FROM videos v 
                LEFT JOIN formats f ON v.video_id = f.video_id 
                WHERE v.video_id = ? 
                GROUP BY v.video_id
            ''', (video_id,))
            result = c.fetchone()
            print(f"Title: {result[0]}")
            print(f"Channel: {result[1]}")
            print(f"Views: {result[2]:,}")
            print(f"Saved to: {result[3]}")
            print(f"Available Formats: {result[4]}")
            
    except Exception as e:
        print(f"An error occurred: {str(e)}")
        conn.rollback()
        raise e
    finally:
        conn.close()

def process_urls_parallel(urls, output_dir, max_workers=5, batch_size=20, db_user='user'):
    # Split urls into batches
    batches = [urls[i:i + batch_size] for i in range(0, len(urls), batch_size)]
    print(f"Processing {len(urls)} URLs in {len(batches)} batches with {max_workers} workers")
    
    # Process batches in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Create a future for each batch
        futures = []
        for batch in batches:
            future = executor.submit(process_batch, batch, output_dir, db_user)
            futures.append(future)
        
        # Process completed batches
        for i, future in enumerate(as_completed(futures)):
            try:
                future.result()  # Will raise any exceptions that occurred
                print(f"Completed batch {i + 1}/{len(batches)}")
            except Exception as e:
                print(f"Batch {i + 1} failed: {str(e)}")

def process_batch(urls, output_dir, db_user='user'):
    for url in urls:
        try:
            print(f"Processing: {url}")
            download_and_store_video(url, output_path=output_dir, db_user=db_user)
        except Exception as e:
            print(f"Failed to process {url}: {str(e)}")

if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='Open TikTok profile page')
    parser.add_argument('username', help='TikTok username (without @)')
    args = parser.parse_args()

    tiktok_dir = Path(Path.home() / "Downloads" / "TikTok")
    txt_files = list(Path(Path.home() / "Downloads" / "TikTok").glob("*.txt"))

    for x in txt_files:
        with open(x, 'r', encoding='utf-8') as f:
            urls = [line.strip() for line in f if line.strip().rsplit("/")[-2] != "photo"]

        collection_dir = Path(tiktok_dir / x.name.split('.')[0])
        print(x.name, collection_dir)

        # with open('tiktok_saved_videos.txt', 'r', encoding='utf-8') as f:
        #     urls = [line.strip() for line in f if line.strip().rsplit("/")[-2] != "photo"]

        print(f"Found {len(urls)} URLs to process for {x.name} collection")
        
        # Configure these values as needed
        MAX_WORKERS = 5  # Number of simultaneous threads
        BATCH_SIZE = 20  # URLs per batch
        
        process_urls_parallel(urls, collection_dir, MAX_WORKERS, BATCH_SIZE, db_user=args.username)