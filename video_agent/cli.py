from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

from .engine import ROOT, build
from .schema import load_project
from .state import Store
from .util import read_json


def doctor(path: Path) -> dict:
    data = read_json(path)
    keys = ["ffmpeg","ffprobe","node","voice_python","remotion_root","browser","voice_model","asr_model"]
    checks = {key:Path(data[key]).exists() for key in keys}
    checks["renderer_dependency_link"] = (ROOT/"renderer/node_modules/remotion").is_dir()
    return {"passed":all(checks.values()),"checks":checks,"scope":"paths only; actual tool execution verified by run"}


def main() -> int:
    parser = argparse.ArgumentParser(description="学习内容视频工具：校验、执行、恢复与检查")
    parser.add_argument("--runtime",type=Path,default=ROOT/"config/local.json")
    commands = parser.add_subparsers(dest="command",required=True)
    commands.add_parser("doctor",help="检查已安装工具路径，不重新安装")
    web = commands.add_parser("web",help="打开本地创作工作台")
    web.add_argument("--port",type=int,default=8765)
    validate = commands.add_parser("validate",help="校验项目配置与口播事件锚点")
    validate.add_argument("project",type=Path)
    status = commands.add_parser("status",help="查看持久任务状态和错误")
    status.add_argument("project",type=Path)
    run = commands.add_parser("run",help="制作配置中的场景；重复运行自动复用有效产物")
    run.add_argument("project",type=Path)
    run.add_argument("--stop-after",help="指定阶段完成后保存进度并暂停，例如 render:S03")
    run.add_argument("--fail-once",help="验收专用：在指定未缓存阶段注入一次可恢复错误")
    args = parser.parse_args()

    def cancel(signum,frame):
        raise KeyboardInterrupt(f"Received signal {signum}")
    signal.signal(signal.SIGTERM,cancel)
    try:
        if args.command == "web":
            from .web import serve
            serve(args.port)
            return 0
        if args.command == "doctor":
            result = doctor(args.runtime)
            print(json.dumps(result,ensure_ascii=False,indent=2))
            return 0 if result["passed"] else 1
        if args.command == "validate":
            result = load_project(args.project)
            print(json.dumps({"valid":True,"project":result["project_id"],"scenes":len(result["scenes"])},ensure_ascii=False))
        elif args.command == "status":
            print(json.dumps(Store(args.project.resolve().parent/".agent").status(),ensure_ascii=False,indent=2))
        else:
            build(args.project,args.runtime,fail_once=args.fail_once,stop_after=args.stop_after)
        return 0
    except KeyboardInterrupt as exc:
        print(str(exc),file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}",file=sys.stderr)
        return 1
