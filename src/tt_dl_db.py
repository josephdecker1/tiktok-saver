from concurrent.futures import ThreadPoolExecutor, as_completed
from yt_dlp import YoutubeDL
from datetime import datetime
from pathlib import Path
from tqdm import tqdm
import threading
import argparse
import hashlib
import sqlite3
import math
import csv
import os

import utils

# Create a global lock for CSV writing
csv_lock = threading.Lock()

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

def log_failed_download(output_path, url, collection_name, error):
    try:
        # Ensure output directory exists
        Path(output_path).mkdir(parents=True, exist_ok=True)
        output_path = Path(str(output_path).rsplit("/", 1)[0])
        
        failed_downloads_file = Path(output_path) / 'failed_downloads.csv'
        print(f"Attempting to log failure to: {failed_downloads_file}")
        
        # Use threading.Lock to ensure thread-safe writing
        with csv_lock:
            file_exists = failed_downloads_file.exists()
            print(f"CSV file exists: {file_exists}")
            
            with open(failed_downloads_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                if not file_exists:
                    print("Creating new CSV file with headers")
                    writer.writerow(['url', 'collection_name', 'error', 'date'])
                print(f"Writing error for URL: {url}")
                writer.writerow([
                    url,
                    collection_name,
                    str(error),
                    datetime.now().isoformat()
                ])
            print("Successfully wrote to CSV")
            
    except Exception as logging_error:
        print(f"Error while logging failure: {str(logging_error)}")
        print(f"Failed to log error for URL: {url}")
        print(f"Original error was: {str(error)}")

def init_database(db_user='user'):
    """Create the database and tables if they don't exist"""

    db_path = utils.get_downloads_dir('TikTok')
    conn = sqlite3.connect(f'{db_path}/tt_metadata_{db_user}.db')
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS collections (
            collection_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_date TEXT,
            url TEXT
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS videos (
        video_id TEXT PRIMARY KEY,
        collection_id TEXT,
        title TEXT,
        channel TEXT,
        duration INTEGER,
        view_count INTEGER,
        like_count INTEGER,
        upload_date TEXT,
        description TEXT,
        download_date TEXT,
        url TEXT,
        filepath TEXT,
        FOREIGN KEY (collection_id) REFERENCES collections(collection_id)
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

    c.execute('''
        CREATE INDEX IF NOT EXISTS idx_videos_collection_id ON videos(collection_id);
    ''')

    # # Add failed_downloads table
    # c.execute('''
    #     CREATE TABLE IF NOT EXISTS failed_downloads (
    #         url TEXT PRIMARY KEY,
    #         collection_id TEXT,
    #         error_message TEXT,
    #         attempt_date TEXT,
    #         FOREIGN KEY (collection_id) REFERENCES collections(collection_id)
    #     )
    # ''')
    
    conn.commit()
    return conn

def download_and_store_video(url, output_path="outputs", db_user='user'):
    try:
        # Configure and store collection name
        collection_name = str(output_path).split('/')[-1]
        
        with YoutubeDL() as ydl:
            print("Extracting video information...")
            info = ydl.extract_info(url, download=False)
            
            video_id = info.get('id')
            video_ext = info['ext'] if 'ext' in info else 'mp4'
            output_filename = f"{video_id}.{video_ext}"
            output_filepath = os.path.join(output_path, output_filename)

            ydl_opts = {
                'format': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]',
                'outtmpl': output_filepath,
                'progress_hooks': [ProgressBar()],
                'quiet': True,
                'no_warnings': True,
            }

            print("Downloading video...")
            download_success = False
            try:
                with YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                print("Download completed!")
                download_success = True
                
            except Exception as download_error:
                log_failed_download(output_path, url, collection_name, download_error)
                print(f"Download failed: {str(download_error)}")

            # Only try database operations if download succeeded
            if download_success:
                # Create a new connection for each successful download
                conn = init_database(db_user)
                try:
                    c = conn.cursor()
                    
                    # Insert collection
                    collection_id = hashlib.sha256(collection_name.encode()).hexdigest()[:16]
                    c.execute('''
                        INSERT OR IGNORE INTO collections (collection_id, name, created_date, url)
                        VALUES (?, ?, ?, ?)
                    ''', (collection_id, collection_name, datetime.now().isoformat(), url))

                    video_data = {
                        'video_id': video_id,
                        'collection_id': collection_id,
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
                        (video_id, collection_id, title, channel, duration, view_count, like_count, 
                        upload_date, description, download_date, url, filepath)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', tuple(video_data.values()))
                    
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
                except Exception as db_error:
                    print(f"Database error: {str(db_error)}")
                    log_failed_download(output_path, url, collection_name, f"Database error: {str(db_error)}")
                finally:
                    conn.close()
            
    except Exception as e:
        print(f"An error occurred: {str(e)}")
        log_failed_download(output_path, url, collection_name, str(e))

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

        if not collection_dir.exists():
            print(f"collection dir {collection_dir} does not exist, creating...")
            try:
                collection_dir.mkdir()
                print("collection dir created")
            except Exception as e:
                print(f"Error creating collection dir: {str(e)}")

        print(f"Found {len(urls)} URLs to process for '{x.name}' collection")
        
        # Configure these values as needed
        MAX_WORKERS = 5  # Number of simultaneous threads
        BATCH_SIZE = 20  # URLs per batch
        
        process_urls_parallel(urls, collection_dir, MAX_WORKERS, BATCH_SIZE, db_user=args.username)
