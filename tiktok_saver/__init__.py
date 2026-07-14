"""tiktok-saver — durable first-party export of your own TikTok Collections,
Favorites (saved) and Likes.

The design separates ENUMERATION (which posts are in which list — read from the
logged-in page's own JSON replies via Playwright response interception) from
DOWNLOAD (fetch the bytes — delegated to yt-dlp for videos and gallery-dl for
photo slideshows). See ARCHITECTURE.md for why.
"""

__version__ = "0.2.0"
