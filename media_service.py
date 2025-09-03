
import instaloader
import shutil
import os
import subprocess
import json
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import uvicorn
import logging

# --- Настройка ---
LOG_FILE = '/root/media_service.log'
RCLONE_REMOTE = "gdrive"
GDRIVE_BASE_PATH = "neurosfera/content/video"
RCLONE_FULL_PATH = f"{RCLONE_REMOTE}:{GDRIVE_BASE_PATH}"
TEMP_DIR = "/tmp/media_downloads"

INSTAGRAM_USERNAME = "unreal_sirena"
INSTAGRAM_SESSION_FILE = "/root/.config/instaloader/session-unreal_sirena"


# --- Логирование ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

# --- FastAPI приложение ---
app = FastAPI()

class ServiceRequest(BaseModel):
    operation_type: str
    context: str

def run_command(command):
    """Запускает команду в оболочке и возвращает ее вывод."""
    logging.info(f"Executing command: {' '.join(command)}")
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, encoding='utf-8')
        logging.info(f"Command stdout: {result.stdout.strip()}")
        if result.stderr:
            logging.warning(f"Command stderr: {result.stderr.strip()}")
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        logging.error(f"Command failed with exit code {e.returncode}")
        logging.error(f"Stderr: {e.stderr.strip()}")
        logging.error(f"Stdout: {e.stdout.strip()}")
        raise
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        raise

@app.on_event("startup")
async def startup_event():
    """При старте приложения создаем необходимые директории."""
    os.makedirs(TEMP_DIR, exist_ok=True)
    logging.info(f"Creating Google Drive directory: {RCLONE_FULL_PATH}")
    try:
        run_command(["rclone", "mkdir", RCLONE_FULL_PATH])
    except Exception as e:
        logging.error(f"Failed to create Google Drive directory: {e}")
        # Не блокируем старт, rclone может справиться с этим позже

@app.post("/media_handler")
async def media_handler(req: ServiceRequest):
    """Основной обработчик запросов."""
    client_ip = "N/A"
    logging.info(f"Received request from {client_ip}: operation='{req.operation_type}', context='{req.context}'")

    if req.operation_type in ["CopyVideoFromUrl", "CopyYouTubeVideo"]:
        video_url = req.context
        if not video_url:
            logging.error("Error: Video URL is empty.")
            return {"status": "error", "detail": "Video URL is empty."}

        platform = "Unknown"
        if "youtube.com" in video_url or "youtu.be" in video_url:
            platform = "YouTube"
        elif "instagram.com" in video_url:
            platform = "Instagram"
        elif "tiktok.com" in video_url:
            platform = "TikTok"
        
        logging.info(f"Detected platform: {platform} for URL: {video_url}")
        if not video_url:
            logging.error("Error: Video URL is empty.")
            return {"status": "error", "detail": "Video URL is empty."}

        try:
            timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
            local_filepath = None

            if platform == "Instagram":
                L = instaloader.Instaloader(dirname_pattern=TEMP_DIR + "/{target}", filename_pattern="{shortcode}")
                try:
                    L.load_session_from_file(INSTAGRAM_USERNAME, INSTAGRAM_SESSION_FILE)
                    logging.info(f"Instaloader session loaded from {INSTAGRAM_SESSION_FILE}")
                except FileNotFoundError:
                    logging.error(f"Instaloader session file not found at {INSTAGRAM_SESSION_FILE}. Please log in manually first.")
                    return {"status": "error", "detail": "Instaloader session not found. Please log in manually."}
                except Exception as e:
                    logging.error(f"Failed to load Instaloader session: {e}")
                    return {"status": "error", "detail": f"Failed to load Instaloader session: {e}"}

                import re
                try:
                    match = re.search(r'(?:reel|p)/([A-Za-z0-9_-]+)', video_url)
                    if not match:
                        raise ValueError("Не удалось извлечь короткий код из URL Instagram.")
                    shortcode = match.group(1)
                    logging.info(f"Извлеченный короткий код Instagram: {shortcode}")

                    post = instaloader.Post.from_shortcode(L.context, shortcode)
                    
                    # Instaloader скачивает в поддиректорию, созданную по shortcode
                    # Мы хотим получить путь к файлу, который был скачан
                    # Instaloader сохраняет файлы с расширением .mp4 для видео
                    
                    # Скачиваем пост
                    L.download_post(post, target=post.shortcode)
                    
                    # Ищем скачанный файл
                    downloaded_files = [f for f in os.listdir(os.path.join(TEMP_DIR, post.shortcode)) if f.endswith(('.mp4', '.jpg', '.jpeg', '.png'))]
                    if not downloaded_files:
                        raise FileNotFoundError(f"No media found for Instagram post {post.shortcode}")
                    
                    # Предпола��аем, что нам нужен первый найденный файл
                    local_filepath = os.path.join(TEMP_DIR, post.shortcode, downloaded_files[0])
                    logging.info(f"Instagram media downloaded to: {local_filepath}")

                except instaloader.exceptions.BadResponseException as e:
                    logging.error(f"Instaloader BadResponseException: {e}")
                    return {"status": "error", "detail": f"Instaloader error: {e}. Check if the post is public or requires login."}
                except Exception as e:
                    logging.error(f"An error occurred during Instagram download: {e}")
                    return {"status": "error", "detail": f"Instagram download failed: {e}"}

            elif platform == "YouTube" or platform == "TikTok": # yt-dlp will handle YouTube and TikTok
                output_template = f"{TEMP_DIR}/{timestamp}_%(title)s.%(ext)s"
                
                yt_dlp_command_base = ["/opt/neurosfera_venv/bin/yt-dlp"]

                proc = subprocess.run(
                    yt_dlp_command_base + ["--get-filename", "-o", output_template, video_url],
                    capture_output=True, text=True, check=True, encoding='utf-8'
                )
                local_filepath = proc.stdout.strip()
                
                logging.info(f"Downloading video to: {local_filepath}")
                run_command(yt_dlp_command_base + ["-o", output_template, video_url])
                
                if not os.path.exists(local_filepath):
                     raise FileNotFoundError(f"Downloaded file not found at {local_filepath}")

            else:
                logging.error(f"Unsupported platform for URL: {video_url}")
                return {"status": "error", "detail": f"Unsupported platform for URL: {video_url}"}

            # 2. Загружаем на Google Drive
            logging.info(f"Uploading {local_filepath} to {RCLONE_FULL_PATH}")
            run_command(["rclone", "copy", local_filepath, RCLONE_FULL_PATH, "--fast-list"])

            # 3. Получаем публичную ссылку
            filename = os.path.basename(local_filepath)
            remote_filepath = f"{GDRIVE_BASE_PATH}/{filename}"
            logging.info(f"Getting public link for {remote_filepath}")
            
            link_output = run_command(["rclone", "link", f"{RCLONE_REMOTE}:{remote_filepath}"])
            
            if not link_output or not link_output.startswith("http"):
                raise Exception("Failed to get public link from rclone.")

            # 4. Очистка
            logging.info(f"Cleaning up local file: {local_filepath}")
            # Instaloader создает директорию для каждого поста, удаляем ее
            if platform == "Instagram":
                import shutil
                shutil.rmtree(os.path.dirname(local_filepath))
            else:
                os.remove(local_filepath)

            logging.info(f"Successfully processed {video_url}. Link: {link_output}")
            return {"status": "success", "gdrive_link": link_output}

        except subprocess.CalledProcessError as e:
            error_message = f"yt-dlp command failed: {e.stderr.strip()}"
            logging.error(error_message)
            return {"status": "error", "detail": error_message}
        except FileNotFoundError as e:
            error_message = f"File not found error: {e}"
            logging.error(error_message)
            return {"status": "error", "detail": error_message}
        except Exception as e:
            error_message = f"An unexpected error occurred: {e}"
            logging.error(error_message)
            return {"status": "error", "detail": error_message}
    else:
        logging.warning(f"Unsupported operation type: {req.operation_type}")
        return {"status": "error", "detail": "Unsupported operation type."}

if __name__ == "__main__":
    logging.info("Starting Media Service...")
    uvicorn.run(app, host="0.0.0.0", port=8002)

