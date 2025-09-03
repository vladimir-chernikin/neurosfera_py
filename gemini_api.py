from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import os
import logging
from logging.handlers import RotatingFileHandler
import gzip
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions

# --- Настройка логирования ---
LOG_FILE = os.path.join(os.path.dirname(__file__), 'gemini_api.log')

def gzip_rotator(source, dest):
    with open(source, 'rb') as f_in, gzip.open(f"{dest}.gz", 'wb', compresslevel=9) as f_out:
        f_out.writelines(f_in)
    os.remove(source)

logger = logging.getLogger('gemini_api_logger')
logger.setLevel(logging.INFO)
handler = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5)
handler.rotator = gzip_rotator
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

# --- Конфигурация Google API ---
try:
    api_key = os.environ.get("GEMINI_API_PROXY_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_PROXY_KEY environment variable not set.")
    genai.configure(api_key=api_key)
except ValueError as e:
    logger.critical(f"API Key configuration error: {e}")
    
# --- FastAPI приложение ---
app = FastAPI()

class GeminiRequest(BaseModel):
    prompt: str
    mode: str | None = "auto"  # "auto", "simple", "medium", "complex"
    temperature: float | None = None
    max_output_tokens: int | None = None
    top_p: float | None = None
    top_k: int | None = None

# --- Вспомогательная функция для вызова API ---
async def _execute_gemini_api(prompt: str, model_name: str, generation_config: genai.types.GenerationConfig | None = None) -> (str | None, str | None):
    """Асинхронно вызывает Google Gemini API и возвращает (ответ, ошибка)."""
    try:
        model = genai.GenerativeModel(model_name)
        response = await model.generate_content_async(
            prompt,
            generation_config=generation_config
        )
        if not response.parts:
             return " ", None
        return response.text, None
    except google_exceptions.GoogleAPICallError as e:
        logger.error(f"Google API Call Error for model {model_name}: {e.message}")
        return None, e.message
    except Exception as e:
        logger.error(f"An unexpected error occurred in API call for model {model_name}: {e}")
        return None, str(e)

@app.post("/run_gemini")
async def run_gemini(req: GeminiRequest, http_request: Request):
    client_ip = http_request.client.host
    logger.info(f"Request from {client_ip}: mode='{req.mode}', temp='{req.temperature}', prompt='{req.prompt[:150]}...'")

    generation_config = genai.types.GenerationConfig(
        temperature=req.temperature,
        max_output_tokens=req.max_output_tokens,
        top_p=req.top_p,
        top_k=req.top_k
    )

    target_model = ""
    
    if req.mode == "auto":
        logger.info("Auto mode selected. Querying router model...")
        router_prompt = f"""Analyze the user's request and classify its complexity. Respond with a single word: 'simple', 'medium', or 'complex'.
- 'simple': Factual questions, translations, summaries.
- 'medium': Code generation, creative writing, multi-step instructions.
- 'complex': Advanced reasoning, strategic planning, architectural design.
User Request: "{req.prompt}" """
        
        router_model = "gemini-1.5-flash"
        router_response, router_error = await _execute_gemini_api(router_prompt, router_model)
        
        if router_error:
            logger.error(f"Router model failed: {router_error}")
            raise HTTPException(status_code=500, detail=f"Router model failed: {router_error}")
        
        router_choice = router_response.lower().strip()
        if "complex" in router_choice:
            target_model = "gemini-2.5-pro"
            logger.info(f"Router chose 'complex'. Routing to {target_model}.")
        elif "medium" in router_choice:
            target_model = "gemini-1.5-pro"
            logger.info(f"Router chose 'medium'. Routing to {target_model}.")
        else:
            target_model = "gemini-1.5-flash"
            logger.info(f"Router chose 'simple'. Routing to {target_model}.")
            
    elif req.mode == "simple":
        target_model = "gemini-1.5-flash"
        logger.info(f"Forcing 'simple' mode. Using {target_model}.")
    elif req.mode == "medium":
        target_model = "gemini-1.5-pro"
        logger.info(f"Forcing 'medium' mode. Using {target_model}.")
    else: # "complex" or any other value defaults to the highest tier
        target_model = "gemini-2.5-pro"
        logger.info(f"Forcing 'complex' mode. Using {target_model}.")

    final_response, final_error = await _execute_gemini_api(
        req.prompt, 
        target_model, 
        generation_config=generation_config
    )

    if final_error:
        logger.error(f"Error for {client_ip} with model '{target_model}': {final_error}")
        raise HTTPException(status_code=500, detail=final_error)
    
    logger.info(f"Success for {client_ip} with model '{target_model}': '{final_response[:200]}...'")
    return {"response": final_response}

if __name__ == "__main__":
    import uvicorn
    logger.info("Starting DIRECT 3-Tier Gemini API proxy server...")
    uvicorn.run(app, host="127.0.0.1", port=8001)

