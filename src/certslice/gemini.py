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
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if data.get("gemini_model") in ["gemini-2.0-flash-lite", "gemini-2.5-flash-lite"] or data.get("model") in ["gemini-2.0-flash-lite", "gemini-2.5-flash-lite"]:
                data["gemini_model"] = "gemini-flash-lite-latest"
                data["model"] = "gemini-flash-lite-latest"
                try:
                    SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
                except Exception:
                    pass
            return data
        except Exception:
            pass
    return {"gemini_api_key": "", "use_ai": False, "model": "gemini-flash-lite-latest", "gemini_model": "gemini-flash-lite-latest"}


def save_user_settings(settings: dict[str, Any] | None = None, **kwargs: Any) -> None:
    try:
        current = load_user_settings()
        if settings and isinstance(settings, dict):
            current.update(settings)
        if kwargs:
            current.update(kwargs)
        SETTINGS_FILE.write_text(json.dumps(current, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not save settings to %s: %s", SETTINGS_FILE, exc)


class GeminiRescue:
    def __init__(self, api_key: str, model: str = "gemini-flash-lite-latest"):
        self.api_key = api_key.strip()
        self.model = model.strip() or "gemini-flash-lite-latest"

    def analyze_certificate_pages(
        self,
        pages: list[pymupdf.Page],
        candidates: list[Candidate],
        employees: list[Employee],
    ) -> dict[str, Any] | None:
        """Analyze certificate pages with Gemini Flash-Lite to extract holder, title, dates, and SKP metadata."""
        if not self.api_key or not pages:
            return None

        # Build parts with prompt and images
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

        prompt = (
            "You are an expert document inspector for certificate verification and employee matching.\n"
            "Analyze the provided certificate page image(s).\n\n"
            f"Here are candidate employees from master data (if any):\n{json.dumps(cand_info, indent=2)}\n\n"
            "Extract information strictly in JSON format matching this schema:\n"
            "{\n"
            '  "matched_option_id": integer or null,\n'
            '  "detected_holder_name": string,\n'
            '  "confidence": "high" | "medium" | "low",\n'
            '  "number": string or null,\n'
            '  "issue_date": "YYYY-MM-DD" or null,\n'
            '  "expiry_date": "YYYY-MM-DD" or null,\n'
            '  "title": string or null,\n'
            '  "is_skp": boolean,\n'
            '  "level": string or null,\n'
            '  "peran": string or null\n'
            "}\n"
            "Rules for SKP (Sertifikasi Kompetensi Pembangkitan / Ketenagalistrikan ESDM):\n"
            "- Set is_skp to true if it is an SKP certificate (e.g. from PT SKP Tenaga Listrik, ESDM, or KemenESDM).\n"
            "- 'level': Extract occupation level (e.g. 'Pelaksana Madya', 'Pelaksana Utama', 'Operator Senior', etc.).\n"
            "- 'peran': Extract competency role/job title on certificate or details (e.g. 'Operator junior lokal peralatan bantu boiler', 'Pemeliharaan Generator PLTD', etc.).\n"
            "- If not an SKP certificate, set is_skp to false, level to null, peran to null, and put the course/certificate name in 'title'.\n"
            "- If none of the candidates match, set matched_option_id to null."
        )

        parts: list[dict[str, Any]] = [{"text": prompt}]
        for p in pages:
            png_bytes = p.get_pixmap(dpi=120).tobytes("png")
            b64_image = base64.b64encode(png_bytes).decode("utf-8")
            parts.append({
                "inline_data": {
                    "mime_type": "image/png",
                    "data": b64_image,
                }
            })

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        payload = {
            "contents": [{"parts": parts}],
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
            with urllib.request.urlopen(req, timeout=35) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))

            content_text = (
                resp_data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            parsed = json.loads(content_text)
            matched_opt = parsed.get("matched_option_id")
            chosen_cand = None
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
                "is_skp": parsed.get("is_skp", False),
                "level": parsed.get("level"),
                "peran": parsed.get("peran"),
            }
        except urllib.error.HTTPError as err:
            err_body = err.read().decode("utf-8", errors="ignore")
            logger.warning("Gemini API HTTP %d error: %s", err.code, err_body)
        except Exception as exc:
            logger.warning("Gemini analysis failed: %s", exc)

        return None

    def batch_analyze_certificates(
        self,
        batch_items: list[dict[str, Any]],
        employees: list[Employee],
    ) -> dict[Any, dict[str, Any]]:
        """Analyze a batch of up to 6 certificates in ONE single Gemini API request.
        
        batch_items: list of dict with keys:
            - 'id': identifier (e.g. integer index or string filename)
            - 'pages': list[pymupdf.Page]
            - 'candidates': list[Candidate]
        """
        if not self.api_key or not batch_items:
            return {}

        prompt = (
            "You are an expert document inspector for certificate verification and employee matching.\n"
            "You will be given multiple certificates to inspect, labeled as 'Document #<ID>'.\n"
            "For each document, analyze the provided page images and candidate employees from master data.\n\n"
            "Return a JSON ARRAY where each element corresponds to one document, strictly matching this schema:\n"
            "[\n"
            "  {\n"
            '    "id": <Document ID>,\n'
            '    "matched_option_id": integer or null,\n'
            '    "detected_holder_name": string,\n'
            '    "confidence": "high" | "medium" | "low",\n'
            '    "number": string or null,\n'
            '    "issue_date": "YYYY-MM-DD" or null,\n'
            '    "expiry_date": "YYYY-MM-DD" or null,\n'
            '    "title": string or null,\n'
            '    "is_skp": boolean,\n'
            '    "level": string or null,\n'
            '    "peran": string or null\n'
            "  }\n"
            "]\n\n"
            "Rules for SKP (Sertifikasi Kompetensi Pembangkitan / Ketenagalistrikan ESDM):\n"
            "- Set is_skp to true if it is an SKP certificate (e.g. PT SKP Tenaga Listrik, ESDM).\n"
            "- 'level': Extract level (e.g. 'Pelaksana Madya', 'Pelaksana Utama', 'Operator Senior', etc.).\n"
            "- 'peran': Extract competency role/job title (e.g. 'Operator junior lokal peralatan bantu boiler', 'Pemeliharaan Generator PLTD').\n"
            "- If not SKP, set is_skp to false, level null, peran null, and put course/training name in 'title'.\n"
            "- If none of the candidates match, set matched_option_id to null."
        )

        parts: list[dict[str, Any]] = [{"text": prompt}]
        item_map: dict[str, dict[str, Any]] = {}

        for item in batch_items:
            doc_id = item["id"]
            cands = item.get("candidates", [])
            item_map[str(doc_id)] = item

            cand_info = []
            for i, c in enumerate(cands[:6]):
                emp = employees[c.index]
                cand_info.append({
                    "option_id": i,
                    "nid": emp.nid,
                    "nama": emp.nama,
                    "score": round(c.score, 1),
                })

            header_text = f"\n=== Document #{doc_id} ===\nCandidate Employees:\n{json.dumps(cand_info, indent=1)}\n"
            parts.append({"text": header_text})

            for p in item.get("pages", []):
                png_bytes = p.get_pixmap(dpi=110).tobytes("png")
                b64_image = base64.b64encode(png_bytes).decode("utf-8")
                parts.append({
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": b64_image,
                    }
                })

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": 0.1,
                "response_mime_type": "application/json",
            },
        }

        results: dict[Any, dict[str, Any]] = {}
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))

            content_text = (
                resp_data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            parsed_list = json.loads(content_text)
            if not isinstance(parsed_list, list):
                parsed_list = [parsed_list]

            for entry in parsed_list:
                e_id_str = str(entry.get("id"))
                orig_item = item_map.get(e_id_str)
                orig_id = orig_item["id"] if orig_item else entry.get("id")
                cands = orig_item.get("candidates", []) if orig_item else []

                matched_opt = entry.get("matched_option_id")
                chosen_cand = None
                if matched_opt is not None and isinstance(matched_opt, int) and 0 <= matched_opt < len(cands[:6]):
                    chosen_cand = cands[matched_opt]

                results[orig_id] = {
                    "candidate": chosen_cand,
                    "confidence": entry.get("confidence", "medium"),
                    "detected_name": entry.get("detected_holder_name"),
                    "number": entry.get("number"),
                    "issue_date": entry.get("issue_date"),
                    "expiry_date": entry.get("expiry_date"),
                    "title": entry.get("title"),
                    "is_skp": entry.get("is_skp", False),
                    "level": entry.get("level"),
                    "peran": entry.get("peran"),
                }
        except urllib.error.HTTPError as err:
            err_body = err.read().decode("utf-8", errors="ignore")
            logger.warning("Gemini Batch API HTTP %d error: %s", err.code, err_body)
        except Exception as exc:
            logger.warning("Gemini Batch analysis failed: %s", exc)

        return results

    def rescue_group(
        self,
        page: pymupdf.Page,
        candidates: list[Candidate],
        employees: list[Employee],
    ) -> dict[str, Any] | None:
        """Attempt to rescue an unmatched certificate page using Gemini Vision/Multimodal."""
        return self.analyze_certificate_pages([page], candidates, employees)
