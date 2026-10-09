# VidTagra

A local full-stack YouTube tags inspector. The browser UI is served by a small Python HTTP server; the backend uses `yt-dlp` to retrieve metadata for public YouTube videos. No API key is needed.

## Run locally

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python server.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Deploy to Vercel

This repository includes a Vercel serverless endpoint in `api/inspect.py` and `vercel.json`. To publish it:

1. Put the project files in a GitHub repository.
2. In Vercel, create a new project and import that repository. Keep the project root at the repository root; Vercel will serve `index.html` and build the Python function under `api/`.
3. Deploy. No environment variables or YouTube API key are needed.

The local `server.py` is for running the app on your computer. Vercel uses `api/inspect.py` for the hosted backend.

The server binds to localhost only. It accepts standard watch URLs, Shorts, embed/live URLs, and `youtu.be` links. A video must be publicly accessible, and it must have tags available to return any tags. YouTube can temporarily throttle or change its public endpoints; update `yt-dlp` if extraction stops working (`python -m pip install --upgrade yt-dlp`).

The displayed relevance percentage is a local, explainable word-overlap estimate using the title and description. It is not a YouTube metric or a ranking prediction.
