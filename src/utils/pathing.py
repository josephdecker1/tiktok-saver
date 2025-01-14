import os
import platform
from pathlib import Path

def get_chrome_data_path():
    """Get Chrome user data directory based on OS"""
    system = platform.system()
    
    if system == "Windows":
        return Path(os.environ['LOCALAPPDATA']) / 'Google' / 'Chrome' / 'User Data'
    elif system == "Darwin":  # macOS
        return Path.home() / 'Library' / 'Application Support' / 'Google' / 'Chrome'
    elif system == "Linux":
        return Path.home() / '.config' / 'google-chrome'
    else:
        raise OSError(f"Unsupported operating system: {system}")

def get_downloads_dir(extra_path=""):
    """Get downloads directory based on OS"""
    return Path.home() / "Downloads" / extra_path

def create_file_path(filename):
    """Create cross-platform file path"""
    return str(Path(filename))

def join_paths(*paths):
    """Join paths in OS-appropriate way"""
    return str(Path(*paths))