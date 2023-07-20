ydl_opts = {
    # Stream selection format.
    # 360p video + AAC 128kpbs audio, 
    # otherwise fallback to "best" according to yt-dlp's logic
    'format': '134+140/mp4+m4a/bestvideo+bestaudio',
    
    # Path to your cookies (this is also deduced from the native --cookie argument)
    # 'cookiefile': "",
    
    # Do not stop on download/postprocessing errors.
    # Can be 'only_download' to ignore only download errors.
    # Default is 'only_download' for CLI, but False for API
    # 'ignoreerrors': 'only_download',
    
    "outtmpl": '%(upload_date)s [%(uploader)s] %(title)s [%(height)s][%(id)s].%(ext)s',
     
    # Need to test this one, but it's not needed in our case anyway:
    # "match_filter": 'is_live',
    
    "live_from_start": True,
    
    # "wait_for_video" = (60, 120),
    
    'postprocessors': [
        {
            # --embed-thumbnail
            'key': 'EmbedThumbnail',
            # already_have_thumbnail = True prevents the file from being deleted after embedding
            'already_have_thumbnail': False
        },
        {
            'key': 'FFmpegMetadata',
            'add_chapters': False,
            'add_metadata': True,
            'add_infojson': False,
        }
    ]
}