from __future__ import annotations

CREATE_URL = "https://mvsep.com/api/separation/create"
GET_URL = "https://mvsep.com/api/separation/get"
DISABLE_PREMIUM_URL = "https://mvsep.com/api/app/disable_premium"

AUDIO_EXTS = {".wav", ".flac", ".mp3", ".m4a", ".ogg", ".aac", ".opus", ".webm"}
EPS = 1e-12

MODELS = {
    "primary_stems": {
        # BS Roformer SW
        "sep_type": 63,
        "output_format": 1,
    },
    "lead_rhythm_guitar": {
        # MVSep Lead/Rhythm Guitar
        "sep_type": 101,
        "add_opt1": "0",
        "output_format": 1,
    },
    "karaoke": {
        # MVSep Karaoke
        "sep_type": 49,
        "add_opt1": "6",
        "add_opt2": "0",
        "output_format": 1,
    },
    "satb": {
        # Optional SATB model. Run only with --satb.
        "sep_type": 111,
        "add_opt1": "3",
        "add_opt2": "0",
        "output_format": 1,
    },
}
