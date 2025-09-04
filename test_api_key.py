#!/usr/bin/env python3
"""
Тестовый скрипт для проверки загрузки API-ключа.
"""

import os
import sys

# Добавляем текущую директорию в путь для импорта
sys.path.insert(0, os.path.dirname(__file__))

def test_load_api_key():
    """Тестирует функцию загрузки API-ключа."""
    try:
        from gemini_api import load_api_key, load_env_file
        
        print("=== Тест загрузки API-ключа ===")
        
        # Проверяем текущие переменные окружения
        current_key = os.environ.get("GEMINI_API_PROXY_KEY")
        print(f"Текущая переменная GEMINI_API_PROXY_KEY: {'установлена' if current_key else 'не установлена'}")
        
        # Проверяем существование файлов
        prod_file = "/etc/neurosfera/neurosfera.env"
        local_file = os.path.join(os.path.dirname(__file__), 'api.env')
        
        print(f"Файл продакшена {prod_file}: {'существует' if os.path.exists(prod_file) else 'не существует'}")
        print(f"Локальный файл {local_file}: {'существует' if os.path.exists(local_file) else 'не существует'}")
        
        # Пытаемся загрузить ключ
        try:
            api_key = load_api_key()
            print(f"✅ API-ключ успешно загружен: {api_key[:10]}...")
            return True
        except ValueError as e:
            print(f"❌ Ошибка загрузки API-ключа: {e}")
            return False
            
    except ImportError as e:
        print(f"❌ Ошибка импорта: {e}")
        return False
    except Exception as e:
        print(f"❌ Неожиданная ошибка: {e}")
        return False

if __name__ == "__main__":
    success = test_load_api_key()
    sys.exit(0 if success else 1)
