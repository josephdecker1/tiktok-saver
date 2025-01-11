from yt_dlp import YoutubeDL
import sqlite3
from datetime import datetime

def init_database():
    """Create the database and tables if they don't exist"""
    conn = sqlite3.connect('tt_metadata.db')
    c = conn.cursor()
    
    # Create videos table
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
            url TEXT
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

def store_video_info(url):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
    }
    
    try:
        # Initialize database
        conn = init_database()
        c = conn.cursor()
        
        # Get video info
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
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
                'url': url
            }
            
            # Insert or replace video data
            c.execute('''
                INSERT OR REPLACE INTO videos 
                (video_id, title, channel, duration, view_count, like_count, 
                upload_date, description, download_date, url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                SELECT v.title, v.channel, v.view_count, COUNT(f.id) as format_count 
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
            
    except Exception as e:
        print(f"An error occurred: {str(e)}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    video_url = "https://www.tiktok.com/@not.that.ellen/video/7453936225116851498"
    store_video_info(video_url)