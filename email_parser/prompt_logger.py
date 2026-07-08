from datetime import datetime
from pathlib import Path

LOG_FILE = Path(__file__).parent.parent / "prompts.log"


def log(kind: str, prompt: str, result: str = ""):
    ts = datetime.now().isoformat(timespec="seconds")
    line = (
        f"[{ts}] {kind}\n"
        f"PROMPT:\n{prompt.strip()}\n"
    )
    if result:
        line += f"RESULT:\n{result.strip()}\n"
    line += "-" * 40 + "\n"
    with open(LOG_FILE, "a") as f:
        f.write(line)
