from flask import Flask, request, jsonify
from flask_cors import CORS
from yt_dlp import YoutubeDL
import os
import logging
import threading
import time
import glob
from datetime import datetime, timedelta
import hashlib

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("youtube-downloader")

# Use Railway's persistent storage
DOWNLOAD_DIR = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', '/data')
MAX_FILE_AGE_HOURS = int(os.environ.get('MAX_FILE_AGE_HOURS', '24'))  # Clean files older than 24 hours
MAX_DURATION_SECONDS = int(os.environ.get('MAX_DURATION_SECONDS', '3600'))  # 1 hour default

# Simple in-memory rate limiting (for basic protection)
download_requests = {}
MAX_REQUESTS_PER_IP = int(os.environ.get('MAX_REQUESTS_PER_IP', '10'))
RATE_LIMIT_WINDOW_MINUTES = int(os.environ.get('RATE_LIMIT_WINDOW_MINUTES', '60'))

def cleanup_old_files():
    """Clean up files older than MAX_FILE_AGE_HOURS"""
    try:
        if not os.path.exists(DOWNLOAD_DIR):
            return
        
        cutoff_time = datetime.now() - timedelta(hours=MAX_FILE_AGE_HOURS)
        pattern = os.path.join(DOWNLOAD_DIR, "*")
        
        for file_path in glob.glob(pattern):
            if os.path.isfile(file_path):
                file_time = datetime.fromtimestamp(os.path.getctime(file_path))
                if file_time < cutoff_time:
                    try:
                        os.remove(file_path)
                        logger.info(f"Cleaned up old file: {file_path}")
                    except Exception as e:
                        logger.error(f"Failed to remove file {file_path}: {e}")
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")

def start_cleanup_thread():
    """Start background thread for periodic cleanup"""
    def cleanup_worker():
        while True:
            cleanup_old_files()
            time.sleep(3600)  # Run every hour
    
    cleanup_thread = threading.Thread(target=cleanup_worker, daemon=True)
    cleanup_thread.start()
    logger.info("Started cleanup background thread")

def check_rate_limit(client_ip):
    """Simple rate limiting check"""
    current_time = datetime.now()
    
    # Clean old entries
    cutoff_time = current_time - timedelta(minutes=RATE_LIMIT_WINDOW_MINUTES)
    download_requests[client_ip] = [
        req_time for req_time in download_requests.get(client_ip, [])
        if req_time > cutoff_time
    ]
    
    # Check if limit exceeded
    if len(download_requests.get(client_ip, [])) >= MAX_REQUESTS_PER_IP:
        return False
    
    # Add current request
    if client_ip not in download_requests:
        download_requests[client_ip] = []
    download_requests[client_ip].append(current_time)
    
    return True

def get_safe_filename(title):
    """Generate a safe filename from video title"""
    # Remove or replace problematic characters
    safe_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_. "
    safe_title = ''.join(c if c in safe_chars else '_' for c in title)
    # Limit length and remove extra spaces
    safe_title = ' '.join(safe_title.split())[:100]
    return safe_title

# Health check endpoint
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "healthy", 
        "service": "youtube-downloader",
        "timestamp": datetime.now().isoformat(),
        "download_dir": DOWNLOAD_DIR,
        "max_duration": MAX_DURATION_SECONDS
    })

# Root endpoint with documentation
@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "message": "YouTube Downloader API",
        "version": "1.1.0",
        "endpoints": {
            "GET /health": "Health check",
            "POST /download": "Download YouTube video",
            "GET /status": "Service status and configuration"
        },
        "limits": {
            "max_duration_seconds": MAX_DURATION_SECONDS,
            "max_requests_per_ip": MAX_REQUESTS_PER_IP,
            "rate_limit_window_minutes": RATE_LIMIT_WINDOW_MINUTES
        }
    })

# Status endpoint
@app.route('/status', methods=['GET'])
def status():
    try:
        disk_usage = os.statvfs(DOWNLOAD_DIR) if os.path.exists(DOWNLOAD_DIR) else None
        file_count = len(glob.glob(os.path.join(DOWNLOAD_DIR, "*"))) if os.path.exists(DOWNLOAD_DIR) else 0
        
        return jsonify({
            "service": "youtube-downloader",
            "status": "running",
            "download_directory": DOWNLOAD_DIR,
            "file_count": file_count,
            "disk_usage": {
                "total_bytes": disk_usage.f_frsize * disk_usage.f_blocks if disk_usage else None,
                "free_bytes": disk_usage.f_frsize * disk_usage.f_bavail if disk_usage else None
            } if disk_usage else None,
            "configuration": {
                "max_duration_seconds": MAX_DURATION_SECONDS,
                "max_file_age_hours": MAX_FILE_AGE_HOURS,
                "max_requests_per_ip": MAX_REQUESTS_PER_IP,
                "rate_limit_window_minutes": RATE_LIMIT_WINDOW_MINUTES
            }
        })
    except Exception as e:
        logger.error(f"Status check failed: {e}")
        return jsonify({"error": "Status check failed", "details": str(e)}), 500

# Download endpoint
@app.route('/download', methods=['POST'])
def download_video():
    client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr)
    
    # Rate limiting check
    if not check_rate_limit(client_ip):
        logger.warning(f"Rate limit exceeded for IP: {client_ip}")
        return jsonify({
            "error": "Rate limit exceeded",
            "message": f"Maximum {MAX_REQUESTS_PER_IP} requests per {RATE_LIMIT_WINDOW_MINUTES} minutes"
        }), 429
    
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON data"}), 400
        
    url = data.get("url")
    if not url:
        logger.error("No URL provided")
        return jsonify({"error": "No URL provided"}), 400

    # Basic URL validation
    if not (url.startswith('http://') or url.startswith('https://')):
        return jsonify({"error": "Invalid URL format"}), 400

    try:
        # Create download directory
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        logger.info(f"Download directory: {DOWNLOAD_DIR}")
        
        # Get video info first to check duration and validate
        ydl_info_opts = {
            'quiet': True,
            'no_warnings': True,
            'extractaudio': False,
            'noplaylist': True,
        }
        
        with YoutubeDL(ydl_info_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
            except Exception as e:
                logger.error(f"Failed to extract video info: {e}")
                return jsonify({
                    "error": "Failed to extract video information",
                    "details": "Invalid URL or video not accessible"
                }), 400
            
            duration = info.get('duration', 0)
            title = info.get('title', 'Unknown')
            uploader = info.get('uploader', 'Unknown')
            
            logger.info(f"Video found: {title} by {uploader}, Duration: {duration}s")
            
            # Duration limit check
            if duration and duration > MAX_DURATION_SECONDS:
                logger.warning(f"Video too long: {duration}s")
                return jsonify({
                    "error": "Video exceeds maximum duration",
                    "max_duration_seconds": MAX_DURATION_SECONDS,
                    "video_duration_seconds": duration
                }), 400
        
        # Generate safe filename
        safe_title = get_safe_filename(title)
        
        # Download the video
        ydl_opts = {
            'outtmpl': f'{DOWNLOAD_DIR}/{safe_title}.%(ext)s',
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'extractaudio': False,
            'writeinfojson': False,
            'writethumbnail': False,
        }
        
        with YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                logger.info(f"Download complete: {filename}")
            except Exception as e:
                logger.error(f"Download failed during extraction: {e}")
                return jsonify({
                    "error": "Download failed",
                    "details": "Failed to download video file"
                }), 500

        # Generate response
        response_data = {
            "title": title,
            "uploader": uploader,
            "filename": os.path.basename(filename),
            "duration": duration,
            "status": "Downloaded successfully",
            "download_time": datetime.now().isoformat(),
            "file_size_bytes": os.path.getsize(filename) if os.path.exists(filename) else None
        }
        
        logger.info(f"Successfully downloaded: {title}")
        return jsonify(response_data)

    except Exception as e:
        logger.error(f"Unexpected error during download: {str(e)}")
        return jsonify({
            "error": "Internal server error", 
            "details": "An unexpected error occurred during download"
        }), 500

# Initialize cleanup thread when app starts
start_cleanup_thread()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

