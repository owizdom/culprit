"""Images from Gemini image generation: logo concepts (the chosen one is redrawn by hand as SVG) and the BRIEF diagrams.

  uv run python brand/gen.py --prompt "..." --out brand/concepts/lens.png --n 2
  GEMINI_IMAGE_MODEL=gemini-3-pro-image uv run python brand/gen.py --prompt-file p.txt --out site/img/brief-system.png --aspect 16:9 --size 2K
Reads GEMINI_API_KEY from the environment or the repo .env.
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={k}"


def api_key():
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip()
    sys.exit("GEMINI_API_KEY not found in the environment or .env")


def generate(prompt, out, n=1, aspect="1:1", size=None):
    image_config = {"aspectRatio": aspect, **({"imageSize": size} if size else {})}
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                       "generationConfig": {"imageConfig": image_config}}).encode()
    written = 0
    for i in range(n):
        req = urllib.request.Request(URL.format(m=MODEL, k=api_key()), data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            sys.exit(f"gemini {e.code}: {e.read()[:400].decode(errors='replace')}")
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        image = next((p["inlineData"]["data"] for p in parts if "inlineData" in p), None)
        if not image:
            print(f"no image on attempt {i + 1}: {' '.join(p.get('text', '') for p in parts)[:300]}")
            continue
        dest = out if n == 1 else out.with_name(f"{out.stem}-{i + 1}{out.suffix}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(base64.b64decode(image))
        print(f"wrote {dest}")
        written += 1
    return written


def main(argv):
    p = argparse.ArgumentParser()
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--prompt")
    src.add_argument("--prompt-file")
    p.add_argument("--out", required=True)
    p.add_argument("--n", type=int, default=1)
    p.add_argument("--aspect", default="1:1")
    p.add_argument("--size", help="1K, 2K or 4K (models that support it)")
    a = p.parse_args(argv)
    prompt = a.prompt or Path(a.prompt_file).read_text()
    return 0 if generate(prompt, Path(a.out), a.n, a.aspect, a.size) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
