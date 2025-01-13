import sqlite3
import os
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm

# Configuration
DB_PATH = '/home/joseph/Code/repos/tiktok-saver/tt_metadata__jdeck_.db'
OUTPUT_DIR = '/home/joseph/Code/repos/tiktok-saver-ui/transcoded_videos'
MAX_WORKERS = 4  # Adjust based on your CPU cores

def ensure_output_dir():
    """Create output directory if it doesn't exist."""
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

def get_video_info(filepath):
    """Get video codec information using ffprobe."""
    try:
        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_streams',
            '-select_streams', 'v:0',
            filepath
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return 'hevc' in result.stdout.lower()
    except Exception as e:
        print(f"Error checking video codec for {filepath}: {e}")
        return False

def transcode_video(video_data):
    """Transcode a single video from H.265 to H.264."""
    video_id, filepath = video_data
    
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return None
    
    # Only transcode if it's HEVC
    if not get_video_info(filepath):
        print(f"Skipping {filepath} - not HEVC")
        return None
    
    # Create output filename
    output_filename = f"{video_id}_h264.mp4"
    output_path = os.path.join(OUTPUT_DIR, output_filename)
    
    # Skip if already transcoded
    if os.path.exists(output_path):
        print(f"Already transcoded: {output_path}")
        return (video_id, output_path)
    
    try:
        cmd = [
            'ffmpeg',
            '-i', filepath,
            '-c:v', 'libx264',     # Use H.264 codec
            '-preset', 'medium',    # Balance between speed and quality
            '-crf', '23',          # Constant quality factor (lower = better quality)
            '-c:a', 'copy',        # Copy audio without re-encoding
            '-movflags', '+faststart',  # Enable web playback
            output_path
        ]
        
        subprocess.run(cmd, check=True, capture_output=True)
        return (video_id, output_path)
    except subprocess.CalledProcessError as e:
        print(f"Error transcoding {filepath}: {e}")
        if os.path.exists(output_path):
            os.remove(output_path)
        return None
    except Exception as e:
        print(f"Unexpected error transcoding {filepath}: {e}")
        if os.path.exists(output_path):
            os.remove(output_path)
        return None

def main():
    ensure_output_dir()
    
    # Connect to database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Get all videos
        cursor.execute("SELECT video_id, filepath FROM videos")
        videos = cursor.fetchall()
        
        print(f"Found {len(videos)} videos to process")
        
        # Process videos in parallel
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Use tqdm for progress bar
            results = list(tqdm(
                executor.map(transcode_video, videos),
                total=len(videos),
                desc="Transcoding videos"
            ))
        
        # Update database with new filepaths
        updated = 0
        for result in results:
            if result:
                video_id, new_path = result
                cursor.execute(
                    "UPDATE videos SET filepath = ? WHERE video_id = ?",
                    (new_path, video_id)
                )
                updated += 1
        
        conn.commit()
        print(f"\nSuccessfully transcoded and updated {updated} videos")
        
    except Exception as e:
        print(f"Error: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    main()