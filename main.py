# /opt/neurosfera_api/main.py
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
import psycopg2, textwrap

DB = dict(dbname="neurosfera_db",
          user="neurosfera_user",
          password="secure_password",
          host="localhost")

app = FastAPI()

HTML_TEMPLATE = """
<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<title>Neurosfera – Главная</title>
<style>
body{font-family:sans-serif;max-width:720px;margin:3rem auto}
textarea{width:100%;padding:.5rem;border:1px solid #999;border-radius:4px}
#cmd{height:80px}  #result{height:260px}
button{margin:.8rem 0;padding:.4rem 1.6rem}
a{margin-right:1.5rem}
</style></head><body>
<h2>100% Консоль управления</h2>
<p>
  <a href="/pgadmin/" target="_blank">Админка СУБД (pgAdmin)</a>
  <a href="https://neurosfera.su:9090" target="_blank">Админка сервера (Cockpit)</a>
  <a href="https://n8n.neurosfera.su" target="_blank">n8n Workflows</a>
</p>
<label>Команда:</label><br>
<textarea id="cmd" placeholder="/add\\nстрока"></textarea><br>
<button onclick="run()">Выполнить</button><br>
<label>Результат:</label><br>
<textarea id="result" readonly></textarea>
<script>
async function run(){
  const r = await fetch("/exec",{method:"POST",
    headers:{"Content-Type":"text/plain;charset=utf-8"},
    body:document.getElementById("cmd").value});
  const t = await r.text();
  document.getElementById("result").value=t;
}
</script>
</body></html>
"""

@app.get("/", response_class=HTMLResponse)
def root(): return HTML_TEMPLATE

@app.post("/exec")
async def exec_cmd(request: Request):
    content = (await request.body()).decode().strip()
    if not content:
        raise HTTPException(400, "пустой ввод")
    first, *rest = content.splitlines()
    if first.lower() == "/add":
        payload = "\n".join(rest).strip()
        if not payload:
            return JSONResponse({"success":0,"message":"нет данных"}, status_code=400)
        with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO test_data (text_data) VALUES (%s)", (payload,))
        return "Запись успешно добавлена."
    elif first.lower() == "/read":
        with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
            cur.execute("SELECT id, text_data, file_path FROM test_data ORDER BY id;")
            rows = cur.fetchall()
        pretty = "\n".join(map(str, rows)) or "(таблица пуста)"
        return pretty
    else:
        return "Неправильная команда."
