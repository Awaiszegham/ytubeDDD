from flask import Flask, request, jsonify
from flask_cors import CORS
from yt_dlp import YoutubeDL
import os
import logging

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("youtube-downloader")

# Use Railway's persistent storage
DOWNLOAD_DIR = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', '/data')

# Health check endpoint
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "service": "youtube-downloader"})

# Root endpoint with documentation
@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "message": "YouTube Downloader API",
        "endpoints": {
            "GET /health": "Health check",
            "POST /download": "Download YouTube video"
        }
    })

# Download endpoint
@app.route('/download', methods=['POST'])
def download_video():
    data = request.get_json()
    url = data.get("url")
    
    if not url:
        logger.error("No URL provided")
        return jsonify({"error": "No URL provided"}), 400

    try:
        # Create download directory
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        logger.info(f"Download directory: {DOWNLOAD_DIR}")
        
        # Get video info first to check duration
        with YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            duration = info.get('duration', 0)
            logger.info(f"Video found: {info['title']}, Duration: {duration}s")
            
            # Duration limit (1 hour)
            if duration > 3600:
                logger.warning(f"Video too long: {duration}s")
                return jsonify({
                    "error": "Video exceeds maximum duration (1 hour)"
                }), 400
        
        # Download the video
        ydl_opts = {
            'outtmpl': f'{DOWNLOAD_DIR}/%(title)s.%(ext)s',
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'noplaylist': True,
            'quiet': True,
        }
        
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            logger.info(f"Download complete: {filename}")

        return jsonify({
            "title": info['title'],
            "filename": os.path.basename(filename),
            "duration": duration,
            "status": "Downloaded successfully"
        })

    except Exception as e:
        logger.error(f"Download failed: {str(e)}")
        return jsonify({"error": "Failed to download video", "details": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)