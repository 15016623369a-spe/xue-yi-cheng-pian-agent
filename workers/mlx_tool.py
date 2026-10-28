"""Runs inside the existing MLX environment; takes explicit JSON inputs."""
from pathlib import Path
import json
import os
import sys

request = json.loads(Path(sys.argv[1]).read_text())
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HOME"] = request["hf_home"]
import mlx.core as mx

if request["task"] == "voice":
    import numpy as np
    import soundfile as sf
    from mlx_audio.tts.utils import load_model
    model = load_model(request["model"])
    import re
    conditioning=request.get("conditioning","icl")
    verifier=None
    if conditioning=="icl":
        # Keep acoustic/text demonstration for the reference's accent/prosody.
        # Speaker-only conditioning is retained as a fallback, not a substitute
        # for likeness. Reject unwanted continuation before joining phrases.
        from mlx_audio.stt.utils import load_model as load_asr
        from mlx_audio.stt.generate import generate_transcription
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
        from video_agent.alignment import check_phrase_transcript
        verifier=load_asr(request["asr_model"])
    # Short phrase generation avoids long silence/runaway on mixed Chinese
    # research text. Retries are local, bounded and recorded; ASR still gates
    # the resulting scene before any rendering.
    # Keep complete short scenes together: this preserves expressive phrasing
    # and makes the ASR prefix check more reliable than isolated tiny phrases.
    chunks_text=[]; remaining=request["text"]
    while len(remaining)>110:
        candidates=[m.end() for m in re.finditer(r"[。！？；，、：,]",remaining[:110])]
        cut=candidates[-1] if candidates and candidates[-1]>=30 else 100
        chunks_text.append(remaining[:cut]);remaining=remaining[cut:]
    if remaining.strip():chunks_text.append(remaining)
    chunks=[];attempts=[];boundaries=[];position=0
    for i,text in enumerate(chunks_text):
        limit=max(220,min(900,len(text)*7))
        for attempt in range(6 if verifier is not None else 3):
            mx.random.seed(request["seed"]+i*31+attempt*997)
            generated=[]
            for result in model.generate(text=text,ref_audio=request["reference_audio"],
                    ref_text=None if request.get("conditioning")=="speaker_embedding" else Path(request["reference_text"]).read_text().strip(),lang_code=request.get("language","chinese"),
                    max_tokens=limit,temperature=.65,top_p=.9,repetition_penalty=1.08):
                generated.append(np.array(result.audio,dtype=np.float32))
            if not generated: continue
            segment=np.concatenate(generated)
            seconds=len(segment)/model.sample_rate
            # Audio tokens are 12.5 Hz. A full output budget usually means
            # failure to finish, rather than a legitimate sentence ending.
            exhausted=seconds>=limit/12.5-.12
            record={"phrase":i,"attempt":attempt+1,"seed":request["seed"]+i*31+attempt*997,"seconds":seconds,"output_budget_exhausted":exhausted}
            attempts.append(record)
            if not exhausted and np.isfinite(segment).all():
                edge=min(round(.008*model.sample_rate),len(segment)//2)
                if edge:
                    ramp=np.sin(np.linspace(0,np.pi/2,edge))**2
                    segment[:edge]*=ramp
                    segment[-edge:]*=ramp[::-1]
                if verifier is not None:
                    prefix=Path(request["output"]).parent/f"phrase-{i:02}-attempt-{attempt+1}"
                    sf.write(prefix.with_suffix(".wav"),segment,model.sample_rate,subtype="PCM_16")
                    recognized=generate_transcription(model=verifier,audio=str(prefix.with_suffix(".wav")),output_path=str(prefix),format="json",language="zh",word_timestamps=True,temperature=0)
                    transcript=json.loads(prefix.with_suffix(".json").read_text())
                    record["asr_text"]=recognized.text
                    try:
                        record["content_check"]=check_phrase_transcript(text,transcript)
                    except ValueError as exc:
                        record["rejected"]=str(exc)
                        print(json.dumps({"phrase_retry":record},ensure_ascii=False),flush=True)
                        continue
                chunks.append(segment)
                chunks.append(np.zeros(round(.065*model.sample_rate),dtype=np.float32))
                boundaries.append({"text":text,"start":position/model.sample_rate,"end":(position+len(segment))/model.sample_rate})
                position+=len(segment)+round(.065*model.sample_rate)
                break
        else:
            raise RuntimeError(f"Speech phrase {i+1} did not finish after bounded local attempts")
    if not chunks:
        raise RuntimeError("TTS generated no chunks")
    audio=np.concatenate(chunks[:-1])
    if not np.isfinite(audio).all() or len(audio)==0:
        raise RuntimeError("TTS generated invalid audio")
    sf.write(request["output"],audio,model.sample_rate,subtype="PCM_16")
    print(json.dumps({"task":"voice","seconds":len(audio)/model.sample_rate,"samples":len(audio),"conditioning":request.get("conditioning","icl"),"reference_silence_seconds":0,"phrase_asr_verified":verifier is not None,"phrases":boundaries,"phrase_attempts":attempts},ensure_ascii=False))
elif request["task"] == "asr":
    from mlx_audio.stt.utils import load_model
    from mlx_audio.stt.generate import generate_transcription
    model = load_model(request["model"])
    result = generate_transcription(model=model,audio=request["audio"],output_path=request["output_prefix"],
                                    format="json",language="zh",word_timestamps=True,temperature=0.0)
    print(json.dumps({"task":"asr","text":result.text},ensure_ascii=False))
else:
    raise ValueError("Unknown MLX task")
