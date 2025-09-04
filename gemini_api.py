from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import os
import logging
from logging.handlers import RotatingFileHandler
import gzip
from typing import Optional, Tuple
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions


def load_api_key() -> str:
    """
    Загружает API-ключ из переменных окружения.
    
    Сначала пытается загрузить из переменной GEMINI_API_PROXY_KEY.
    Если не найдена, пытается загрузить из файла api.env для локальной разработки.
    
    Returns:
        str: API-ключ для Gemini
        
    Raises:
        ValueError: Если ключ не найден ни в переменных окружения, ни в файле
    """
    # Сначала проверяем переменные окружения
    api_key = os.environ.get("GEMINI_API_PROXY_KEY")
    if api_key:
        return api_key
    
    # Для локальной разработки пытаемся загрузить из api.env
    env_file = os.path.join(os.path.dirname(__file__), 'api.env')
    if os.path.exists(env_file):
        with open(env_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('GEMINI_API_PROXY_KEY='):
                    return line.split('=', 1)[1]
    
    raise ValueError("GEMINI_API_PROXY_KEY не найден в переменных окружения или в файле api.env")


def setup_logging() -> logging.Logger:
    """
    Настраивает логирование с ротацией файлов и сжатием.
    
    Returns:
        logging.Logger: Настроенный логгер
    """
    log_file = os.path.join(os.path.dirname(__file__), 'gemini_api.log')
    
    def gzip_rotator(source: str, dest: str) -> None:
        """Сжимает старые лог-файлы в gzip."""
        with open(source, 'rb') as f_in, gzip.open(f"{dest}.gz", 'wb', compresslevel=9) as f_out:
            f_out.writelines(f_in)
        os.remove(source)
    
    logger = logging.getLogger('gemini_api_logger')
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5)
    handler.rotator = gzip_rotator
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger


def configure_gemini_api() -> None:
    """
    Настраивает Google Gemini API с ключом из переменных окружения.
    
    Raises:
        ValueError: Если не удалось загрузить API-ключ
    """
    try:
        api_key = load_api_key()
        genai.configure(api_key=api_key)
        logger.info("Gemini API успешно настроен")
    except ValueError as e:
        logger.critical(f"Ошибка настройки API-ключа: {e}")
        raise


# Инициализация логирования и API
logger = setup_logging()
configure_gemini_api()
    
# FastAPI приложение
app = FastAPI()


class GeminiRequest(BaseModel):
    """Модель запроса к Gemini API."""
    prompt: str
    mode: Optional[str] = "auto"  # "auto", "simple", "medium", "complex"
    temperature: Optional[float] = None
    max_output_tokens: Optional[int] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None


async def execute_gemini_api(
    prompt: str, 
    model_name: str, 
    generation_config: Optional[genai.types.GenerationConfig] = None
) -> Tuple[Optional[str], Optional[str]]:
    """
    Асинхронно вызывает Google Gemini API.
    
    Args:
        prompt: Текст запроса
        model_name: Название модели Gemini
        generation_config: Конфигурация генерации
        
    Returns:
        Tuple[Optional[str], Optional[str]]: (ответ, ошибка)
    """
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
        logger.error(f"Ошибка Google API для модели {model_name}: {e.message}")
        return None, e.message
    except Exception as e:
        logger.error(f"Неожиданная ошибка при вызове API для модели {model_name}: {e}")
        return None, str(e)

def create_generation_config(req: GeminiRequest) -> genai.types.GenerationConfig:
    """
    Создает конфигурацию генерации на основе запроса.
    
    Args:
        req: Запрос к API
        
    Returns:
        genai.types.GenerationConfig: Конфигурация генерации
    """
    return genai.types.GenerationConfig(
        temperature=req.temperature,
        max_output_tokens=req.max_output_tokens,
        top_p=req.top_p,
        top_k=req.top_k
    )


async def determine_model_by_router(prompt: str) -> str:
    """
    Определяет модель через роутер на основе сложности запроса.
    
    Args:
        prompt: Текст запроса пользователя
        
    Returns:
        str: Название выбранной модели
        
    Raises:
        HTTPException: Если роутер не смог определить модель
    """
    router_prompt = f"""Проанализируй запрос пользователя и классифицируй его сложность. 
Ответь одним словом: 'simple', 'medium', или 'complex'.

- 'simple': Фактические вопросы, переводы, краткие изложения.
- 'medium': Генерация кода, творческое письмо, многошаговые инструкции.
- 'complex': Продвинутые рассуждения, стратегическое планирование, архитектурное проектирование.

Запрос пользователя: "{prompt}" """
    
    router_model = "gemini-1.5-flash"
    router_response, router_error = await execute_gemini_api(router_prompt, router_model)
    
    if router_error:
        logger.error(f"Ошибка роутера: {router_error}")
        raise HTTPException(status_code=500, detail=f"Ошибка роутера: {router_error}")
    
    router_choice = router_response.lower().strip()
    if "complex" in router_choice:
        return "gemini-2.5-pro"
    elif "medium" in router_choice:
        return "gemini-1.5-pro"
    else:
        return "gemini-1.5-flash"


def select_model(mode: str, prompt: str) -> str:
    """
    Выбирает модель на основе режима работы.
    
    Args:
        mode: Режим работы ("auto", "simple", "medium", "complex")
        prompt: Текст запроса (используется для режима "auto")
        
    Returns:
        str: Название выбранной модели
    """
    model_mapping = {
        "simple": "gemini-1.5-flash",
        "medium": "gemini-1.5-pro",
        "complex": "gemini-2.5-pro"
    }
    
    if mode == "auto":
        # Для режима auto модель будет определена через роутер
        return "auto"
    else:
        return model_mapping.get(mode, "gemini-2.5-pro")


@app.post("/run_gemini")
async def run_gemini(req: GeminiRequest, http_request: Request) -> dict:
    """
    Основной эндпоинт для обработки запросов к Gemini API.
    
    Args:
        req: Запрос с параметрами
        http_request: HTTP-запрос для получения IP клиента
        
    Returns:
        dict: Ответ с результатом генерации
        
    Raises:
        HTTPException: При ошибках обработки запроса
    """
    client_ip = http_request.client.host
    logger.info(f"Запрос от {client_ip}: режим='{req.mode}', температура='{req.temperature}', промпт='{req.prompt[:150]}...'")

    generation_config = create_generation_config(req)
    
    # Определяем модель
    if req.mode == "auto":
        logger.info("Выбран автоматический режим. Запрос к роутеру...")
        target_model = await determine_model_by_router(req.prompt)
        logger.info(f"Роутер выбрал модель: {target_model}")
    else:
        target_model = select_model(req.mode, req.prompt)
        logger.info(f"Принудительный режим '{req.mode}'. Используется {target_model}.")

    # Выполняем запрос к выбранной модели
    final_response, final_error = await execute_gemini_api(
        req.prompt, 
        target_model, 
        generation_config=generation_config
    )

    if final_error:
        logger.error(f"Ошибка для {client_ip} с моделью '{target_model}': {final_error}")
        raise HTTPException(status_code=500, detail=final_error)
    
    logger.info(f"Успех для {client_ip} с моделью '{target_model}': '{final_response[:200]}...'")
    return {"response": final_response}

if __name__ == "__main__":
    import uvicorn
    logger.info("Запуск 3-уровневого прокси-сервера Gemini API...")
    uvicorn.run(app, host="127.0.0.1", port=8001)

