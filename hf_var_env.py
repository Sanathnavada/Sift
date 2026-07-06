from pathlib import Path
from dotenv import dotenv_values
from huggingface_hub import HfApi

SPACE_ID = "SanathKnavada/Sift"
ENV_FILE = ".env.docker"

# Put sensitive keys here.
SECRET_KEYS = {
    "SPOTIPY_CLIENT_ID",
    "SPOTIPY_CLIENT_SECRET",
    "OPENAI_API_KEY",
    "HF_TOKEN",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "API_HASH",
    "API_ID",
}

api = HfApi()
env = dotenv_values(ENV_FILE)

for key, value in env.items():
    if not key or value is None:
        continue

    value = str(value).strip()

    if not value:
        continue

    if key in SECRET_KEYS or "KEY" in key or "SECRET" in key or "PASSWORD" in key:
        print(f"Adding secret: {key}")
        api.add_space_secret(repo_id=SPACE_ID, key=key, value=value)
    else:
        print(f"Adding variable: {key}")
        api.add_space_variable(repo_id=SPACE_ID, key=key, value=value)

print("Done.")