# -*- coding: utf-8 -*-
from pathlib import Path

keys = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_PUBLIC_CHANNEL_ID",
    "ALLOWED_ADMIN_IDS",
    "TELEGRAM_ADMIN_CHAT_ID",
    "NEWSROOM_LOGIN_URL",
    "NEWSROOM_CREATE_URL",
    "SEMANTIC_DEDUP_BASELINE_RSS",
)
lines = Path(".env").read_text(encoding="utf-8").splitlines()
for k in keys:
    hits = [l for l in lines if l.strip().startswith(k + "=")]
    if not hits:
        print(k, "= MISSING")
        continue
    v = hits[-1].split("=", 1)[1].strip().strip('"').strip("'")
    if "TOKEN" in k or "PASSWORD" in k:
        print(k, "=", (v[:8] + "...") if v else "EMPTY")
    else:
        print(k, "=", v if v else "EMPTY")
