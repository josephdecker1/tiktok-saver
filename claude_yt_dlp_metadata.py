from yt_dlp import YoutubeDL
import json

def get_video_info(url):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
    }
    
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Print formatted metadata
            print("\nVideo Metadata:")
            print(f"Title: {info.get('title')}")
            print(f"Channel: {info.get('channel')}")
            print(f"Duration: {info.get('duration')} seconds")
            print(f"View Count: {info.get('view_count'):,}")
            print(f"Like Count: {info.get('like_count'):,}")
            print(f"Upload Date: {info.get('upload_date')}")
            print(f"Description: {info.get('description')[:200]}...")  # First 200 chars
            
            # Print all available formats
            print("\nAvailable Formats:")
            for f in info['formats']:
                print(f"Format ID: {f.get('format_id')}, "
                      f"Resolution: {f.get('resolution', 'N/A')}, "
                      f"FileSize: {f.get('filesize', 'N/A')}")
            
            # Optionally save full metadata to file
            with open('video_metadata.json', 'w', encoding='utf-8') as f:
                json.dump(info, f, indent=2, ensure_ascii=False)
            
    except Exception as e:
        print(f"An error occurred: {str(e)}")

if __name__ == "__main__":
    video_url = "https://www.tiktok.com/@not.that.ellen/video/7453936225116851498"
    get_video_info(video_url)