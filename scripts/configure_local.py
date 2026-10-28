"""Connect an existing toolkit; never downloads or installs dependencies."""
from pathlib import Path
import argparse
import json
import shutil

root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description='连接已有视频制作环境，不安装或下载')
p.add_argument('--toolkit',type=Path,required=True)
p.add_argument('--node',default=shutil.which('node'))
args=p.parse_args()
tool=args.toolkit.expanduser().resolve()
config={'ffmpeg':shutil.which('ffmpeg') or '/opt/homebrew/bin/ffmpeg',
        'ffprobe':shutil.which('ffprobe') or '/opt/homebrew/bin/ffprobe',
        'node':args.node,'voice_python':str(tool/'voice/.venv/bin/python'),
        'remotion_root':str(tool/'remotion'),
        'browser':str(tool/'remotion/node_modules/.remotion/chrome-headless-shell/mac-arm64/chrome-headless-shell-mac-arm64/chrome-headless-shell'),
        'voice_model':str(tool/'voice/models/Qwen3-TTS-12Hz-0.6B-Base-8bit'),
        'asr_model':str(tool/'voice/models/whisper-large-v3-turbo-asr-fp16'),
        'hf_home':str(tool/'voice/hf-cache')}
for key,value in config.items():
    if value is None or not Path(value).exists():
        raise SystemExit(f'Missing {key}: {value}; provide an existing installation')
link=root/'renderer/node_modules'
if link.is_symlink():
    if link.resolve()!=(tool/'remotion/node_modules').resolve():
        link.unlink()
elif link.exists():
    raise SystemExit('renderer/node_modules already exists as a directory; leaving it unchanged')
if not link.exists():
    link.symlink_to(tool/'remotion/node_modules',target_is_directory=True)
(root/'config').mkdir(exist_ok=True)
(root/'config/local.json').write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'configured':True,'toolkit':str(tool),'installed_packages':False},ensure_ascii=False))
