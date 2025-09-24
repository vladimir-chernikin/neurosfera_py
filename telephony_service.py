
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import subprocess
import requests
import os
import logging

# Logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

def send_telegram_message(message):
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram bot token or chat ID is not set.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send Telegram message: {e}")

def check_and_start_baresip():
    try:
        # Check if baresip is running
        result = subprocess.run(["pgrep", "-f", "baresip"], capture_output=True, text=True)
        if result.stdout:
            message = "SIP client is already running."
            logger.info(message)
            send_telegram_message(message)
            return

        # Start baresip
        logger.info("SIP client is not running. Starting it...")
        subprocess.Popen(["baresip", "-f", "/root/.baresip"])
        
        # Verify it started
        result = subprocess.run(["pgrep", "-f", "baresip"], capture_output=True, text=True)
        if result.stdout:
            message = "SIP client was successfully started by the service."
            logger.info(message)
            send_telegram_message(message)
        else:
            message = "Attempt to start SIP client failed."
            logger.error(message)
            send_telegram_message(message)

    except Exception as e:
        message = f"An error occurred while checking or starting SIP client: {e}"
        logger.error(message)
        send_telegram_message(message)

@app.on_event("startup")
async def startup_event():
    check_and_start_baresip()

class CallRequest(BaseModel):
    phone_number: str

@app.post("/call")
async def make_call(call_request: CallRequest):
    logger.info(f"Received call request for {call_request.phone_number}")
    send_telegram_message(f"Initiating call to {call_request.phone_number}")
    # Here you would add the logic to interact with baresip to make the call
    return {"message": f"Calling {call_request.phone_number}"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8087)
