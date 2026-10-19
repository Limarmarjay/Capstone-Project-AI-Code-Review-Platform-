from pathlib import Path

from dotenv import load_dotenv

# .env lives at the repo root, a sibling of backend/ — load it before any
# app module reads os.environ (e.g. app.crypto's ENCRYPTION_KEY) at import time.
load_dotenv(Path(__file__).resolve().parent.parent / '.env')

from app import create_app  # noqa: E402

app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=5002)
