"""Start the user's local app independently of the launcher terminal."""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
url="http://127.0.0.1:8765"
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def ready():
    try:
        with opener.open(url+"/api/bootstrap",timeout=2) as response:
            return "token" in json.load(response)
    except Exception:
        return False

if not ready():
    state=ROOT/".web";state.mkdir(exist_ok=True)
    with (state/"server.log").open("ab") as log:
        process=subprocess.Popen([sys.executable,"-m","video_agent","web","--port","8765"],cwd=ROOT,
            stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    (state/"server.pid").write_text(str(process.pid))
    for _ in range(40):
        if ready():break
        if process.poll() is not None:raise SystemExit("工作台未能启动，请查看 .web/server.log")
        time.sleep(.1)
    else:raise SystemExit("服务启动超时，请查看 .web/server.log")
print("本地工作台已就绪："+url)
