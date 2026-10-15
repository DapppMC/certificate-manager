from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any
import urllib.error
import urllib.request

import pymupdf

from certslice.models import Candidate, Employee

logger = logging.getLogger(__name__)

SETTINGS_FILE = Path.home() / ".certslice_settings.json"


def load_user_settings() -> dict[str, Any]:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"gemini_api_key": "", "use_ai": False, "model": "gemini-2.5-flash"}


def save_user_settings(settings: dict[str, Any]) -> None:
    try:
        current = load_user_settings()
        current.update(settings)
        SETTINGS_FILE.write_text(json.dumps(current, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not save settings to %s: %s", SETTINGS_FILE, exc)


class GeminiRescue:
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.api_key = api_key.strip()
        self.model = model.strip() or "gemini-2.5-flash"

    def rescue_group(
        self,
        page: pymupdf.Page,
        candidates: list[Candidate],
        employees: list[Employee],
    ) -> dict[str, Any] | None:
        """Attempt to rescue an unmatched certificate page using Gemini Vision/Multimodal."""
        if not self.api_key or not candidates:
            return None

        # Render page to PNG bytes
        png_bytes = page.get_pixmap(dpi=150).tobytes("png")
        b64_image = base64.b64encode(png_bytes).decode("utf-8")

        # Provide candidate choices to Gemini
        cand_info = []
        for i, c in enumerate(candidates[:8]):
            emp = employees[c.index]
            cand_info.append({
                "option_id": i,
                "nid": emp.nid,
                "nama": emp.nama,
                "score": round(c.score, 1),
                "is_partial": c.partial,
            })

        system_instruction = (
            "You are an expert document inspector for certificate slicing and employee matching. "
            "You will be given an image of a certificate and a list of candidate employees from the master database. "
            "Determine the certificate recipient/holder name, and match it against the candidate list. "
            "Handle abbreviations, shortened names (e.g. 'M. Rizky' for 'Muhammad Rizky', 'Siti R' for 'Siti Rahmawati'), "
            "and academic titles. "
            "If none of the candidates match, set matched_option_id to null."
        )

        prompt = (
            f"Here are the candidate employees from the master data:\n{json.dumps(cand_info, indent=2)}\n\n"
            "Analyze the certificate image and identify the holder name, matching option_id, certificate number, "
            "issue date (YYYY-MM-DD), expiry date (YYYY-MM-DD), and title.\n"
            "Respond strictly in valid JSON with this schema:\n"
            "{\n"
            '  "matched_option_id": integer or null,\n'
            '  "detected_holder_name": string,\n'
            '  "confidence": "high" | "medium" | "low",\n'
            '  "number": string or null,\n'
            '  "issue_date": "YYYY-MM-DD" or null,\n'
            '  "expiry_date": "YYYY-MM-DD" or null,\n'
            '  "title": string or null\n'
            "}"
        )

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": b64_image,
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "response_mime_type": "application/json",
            },
        }

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))

            content_text = (
                resp_data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            parsed = json.loads(content_text)
            matched_opt = parsed.get("matched_option_id")
            if matched_opt is not None and isinstance(matched_opt, int) and 0 <= matched_opt < len(candidates[:8]):
                chosen_cand = candidates[matched_opt]
                return {
                    "candidate": chosen_cand,
                    "confidence": parsed.get("confidence", "medium"),
                    "detected_name": parsed.get("detected_holder_name"),
                    "number": parsed.get("number"),
                    "issue_date": parsed.get("issue_date"),
                    "expiry_date": parsed.get("expiry_date"),
                    "title": parsed.get("title"),
                }
        except urllib.error.HTTPError as err:
            err_body = err.read().decode("utf-8", errors="ignore")
            logger.warning("Gemini API HTTP %d error: %s", err.code, err_body)
        except Exception as exc:
            logger.warning("Gemini rescue failed: %s", exc)

        return None
