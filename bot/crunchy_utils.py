import os

def get_crunchy_cookies_path() -> str | None:
    for p in ["crunchyroll_cookies.txt", "telegram-bot/crunchyroll_cookies.txt",
              "cookies.txt", "telegram-bot/cookies.txt"]:
        if os.path.exists(p) and os.path.getsize(p) > 0:
            return p
    return None
