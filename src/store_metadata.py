from yt_dlp import YoutubeDL
import sqlite3
from datetime import datetime
import os

def init_database(db_user='user'):
    """Create the database and tables if they don't exist"""
    conn = sqlite3.connect(f'tt_metadata_{db_user}.db')
    c = conn.cursor()
    
    # Create videos table with new file_path column
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
            file_path TEXT
        )
    ''')
    
    # Create formats table for available video formats
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

def store_video_info(url, download_dir='downloads', db_user='user'):
    """
    Store video information and download the video
    
    Args:
        url (str): Video URL
        download_dir (str): Directory where videos should be downloaded
    """
    # Create download directory if it doesn't exist
    os.makedirs(download_dir, exist_ok=True)
    
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'outtmpl': os.path.join(download_dir, '%(id)s.%(ext)s'),
        'format': 'best',  # You can modify this to select specific format
    }
    
    try:
        # Initialize database
        conn = init_database(db_user)
        c = conn.cursor()
        
        # Get video info and download
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            # Get the actual file path
            file_ext = info.get('ext', 'mp4')
            file_path = os.path.join(
                download_dir,
                f"{info.get('id')}.{file_ext}"
            )
            
            # Verify file exists
            if not os.path.exists(file_path):
                print(f"Warning: Expected file not found at {file_path}")
                file_path = None
            
            # Insert into videos table
            video_data = {
                'video_id': info.get('id'),
                'title': info.get('title'),
                'channel': info.get('channel'),
                'duration': info.get('duration'),
                'view_count': info.get('view_count'),
                'like_count': info.get('like_count'),
                'upload_date': info.get('upload_date'),
                'description': info.get('description'),
                'download_date': datetime.now().isoformat(),
                'url': url,
                'file_path': file_path
            }
            
            # Insert or replace video data
            c.execute('''
                INSERT OR REPLACE INTO videos 
                (video_id, title, channel, duration, view_count, like_count, 
                upload_date, description, download_date, url, file_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', tuple(video_data.values()))
            
            # Insert format information
            # First delete old formats for this video
            c.execute('DELETE FROM formats WHERE video_id = ?', (info.get('id'),))
            
            # Insert new formats
            for fmt in info['formats']:
                format_data = (
                    info.get('id'),
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
            
            # Example query to verify data
            print("\nStored Data:")
            c.execute('''
                SELECT v.title, v.channel, v.view_count, COUNT(f.id) as format_count, v.file_path 
                FROM videos v 
                LEFT JOIN formats f ON v.video_id = f.video_id 
                WHERE v.video_id = ? 
                GROUP BY v.video_id
            ''', (info.get('id'),))
            result = c.fetchone()
            print(f"Title: {result[0]}")
            print(f"Channel: {result[1]}")
            print(f"Views: {result[2]:,}")
            print(f"Available Formats: {result[3]}")
            print(f"File Location: {result[4]}")
            
    except Exception as e:
        print(f"An error occurred: {str(e)}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    video_url = "https://www.tiktok.com/@not.that.ellen/video/7453936225116851498"
    store_video_info(video_url, download_dir='downloads')