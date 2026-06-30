# MVSEP Pipeline

A simple Python pipeline for running audio source separation with the MVSEP API. It supports local audio files and URLs, downloads/extracts audio when needed, and saves separated stems into an output folder.

## Setup

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it:

```bash
source .venv/bin/activate
```

On Windows Git Bash:

```bash
source .venv/Scripts/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Copy the example environment file:

```bash
cp .env.example .env
```

Open `.env` and add your MVSEP API token:

```env
MVSEP_API_TOKEN=your_token_here
```

## Run

Separate a local audio file:

```bash
python main.py "song.wav" --out "splits"
```

Separate audio from a URL:

```bash
python main.py "https://youtube.com/..." --out "splits"
```

The separated files will be saved in the selected output folder.