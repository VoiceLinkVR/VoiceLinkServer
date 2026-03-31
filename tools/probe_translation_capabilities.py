import argparse
import json
import os
from pathlib import Path
from typing import Any

import httpx

LANG_CANONICAL_MAPPING = {
    "zh": "zh",
    "zh-cn": "zh",
    "zh-sg": "zh",
    "zh-hans": "zh",
    "zt": "zt",
    "zh-hant": "zt",
    "zh-tw": "zt",
    "zh-hk": "zt",
    "zh-mo": "zt",
}

STATIC_FALLBACK_LANGUAGES = [
    "ar", "az", "bg", "bn", "ca", "cs", "da", "de", "el", "en",
    "eo", "es", "et", "eu", "fa", "fi", "fr", "ga", "gl", "he",
    "hi", "hu", "id", "it", "ja", "ko", "lt", "lv", "ms", "nb",
    "nl", "pl", "pt", "ro", "ru", "sk", "sl", "sq", "sv", "th",
    "tl", "tr", "uk", "ur", "zh", "zt",
]

GEMMA_LANGUAGE_CODES = {
    "ar", "az", "bg", "bn", "ca", "cs", "da", "de", "el", "en",
    "eo", "es", "et", "eu", "fa", "fi", "fr", "ga", "gl", "he",
    "hi", "hu", "id", "it", "ja", "ko", "lt", "lv", "ms", "nb",
    "nl", "pl", "pt", "ro", "ru", "sk", "sl", "sq", "sv", "th",
    "tl", "tr", "uk", "ur", "zh", "zt",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe dynamic translation language aggregation and related backends."
    )
    parser.add_argument(
        "--gemma-url",
        default="http://192.168.2.167:38080",
        help="TranslateGemma/llama-server base URL.",
    )
    parser.add_argument(
        "--asr-openapi-url",
        default="http://192.168.2.198:8000/openapi.json",
        help="Speech recognition backend OpenAPI URL.",
    )
    parser.add_argument(
        "--enable-gemma",
        action="store_true",
        help="Force include the local TranslateGemma capability in aggregation.",
    )
    parser.add_argument(
        "--translator-services",
        default=None,
        help="Override TRANSLATOR_SERVICES_LIST for this probe run.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="HTTP timeout in seconds for backend probing.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    return parser.parse_args()


def load_dotenv_like(repo_root: Path) -> None:
    env_path = repo_root / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def normalize_translation_language_code(lang: str) -> str:
    raw = (lang or "").strip().lower()
    if not raw:
        return raw
    if raw in LANG_CANONICAL_MAPPING:
        return LANG_CANONICAL_MAPPING[raw]
    if "-" in raw:
        base = raw.split("-", 1)[0]
        return LANG_CANONICAL_MAPPING.get(base, base)
    return raw


def get_bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_translator_services(args: argparse.Namespace) -> list[str]:
    if args.translator_services:
        return [item.strip() for item in args.translator_services.split(",") if item.strip()]

    configured = os.environ.get("TRANSLATOR_SERVICES_LIST", "bing,iciba,alibaba,MyMemory,google")
    return [item.strip() for item in configured.split(",") if item.strip()]


def safe_get_translator_capabilities(services: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    os.environ.setdefault("translators_default_region", "CN")
    try:
        import translators as ts
    except Exception as exc:  # pylint: disable=broad-except
        warnings.append(f"failed to import translators: {exc}")
        return [], warnings

    pool = getattr(ts, "translators_pool", [])
    pool_map = {name.lower(): name for name in pool}
    candidates = [pool_map[item.lower()] for item in services if item.lower() in pool_map] or list(pool)

    engines: list[dict[str, Any]] = []
    for name in candidates:
        try:
            language_map = ts.get_languages(name) or {}
            if isinstance(language_map, dict):
                engines.append({"engine": name, "languages": language_map})
            else:
                warnings.append(f"{name}: non-dict language map")
        except Exception as exc:  # pylint: disable=broad-except
            warnings.append(f"{name}: {exc}")
    return engines, warnings


def build_gemma_capability_entry() -> dict[str, Any]:
    language_map = {
        source: [target for target in sorted(GEMMA_LANGUAGE_CODES) if target != source]
        for source in sorted(GEMMA_LANGUAGE_CODES)
    }
    return {
        "engine": "translategemma",
        "languages": language_map,
        "endpoint": "/completion",
        "apiMode": os.environ.get("GEMMA_TRANSLATE_API_MODE", "completion"),
    }


def aggregate_runtime_languages(engines: list[dict[str, Any]]) -> dict[str, list[str]]:
    source_languages = set()
    target_languages = set()

    for engine in engines:
        language_map = engine.get("languages", {}) if isinstance(engine, dict) else {}
        if not isinstance(language_map, dict):
            continue

        for raw_source, raw_targets in language_map.items():
            source = normalize_translation_language_code(str(raw_source))
            if source and source != "auto":
                source_languages.add(source)

            targets_iter = raw_targets.keys() if isinstance(raw_targets, dict) else raw_targets
            if isinstance(targets_iter, str):
                targets_iter = [targets_iter]
            if not hasattr(targets_iter, "__iter__"):
                continue

            for raw_target in targets_iter:
                target = normalize_translation_language_code(str(raw_target))
                if target and target != "auto":
                    target_languages.add(target)

    if not source_languages:
        source_languages.update(STATIC_FALLBACK_LANGUAGES)
    if not target_languages:
        target_languages.update(STATIC_FALLBACK_LANGUAGES)

    return {
        "sources": sorted(source_languages),
        "targets": sorted(target_languages),
    }


def summarize_engine_languages(language_map: dict[str, Any]) -> dict[str, Any]:
    sources = set()
    targets = set()

    for raw_source, raw_targets in language_map.items():
        sources.add(normalize_translation_language_code(str(raw_source)))
        targets_iter = raw_targets.keys() if isinstance(raw_targets, dict) else raw_targets
        if isinstance(targets_iter, str):
            targets_iter = [targets_iter]
        if not hasattr(targets_iter, "__iter__"):
            continue
        for raw_target in targets_iter:
            targets.add(normalize_translation_language_code(str(raw_target)))

    return {
        "source_count": len(sources),
        "target_count": len(targets),
        "supports_vi_as_source": "vi" in sources,
        "supports_vi_as_target": "vi" in targets,
        "sample_sources": sorted(sources)[:20],
        "sample_targets": sorted(targets)[:20],
    }


def fetch_json(url: str, timeout: float) -> dict[str, Any]:
    with httpx.Client(timeout=timeout) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.json()


def probe_get(url: str, timeout: float) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url)
            payload = {
                "ok": True,
                "status_code": response.status_code,
                "content_type": response.headers.get("content-type", ""),
            }
            try:
                data = response.json()
                if isinstance(data, dict):
                    payload["json_keys"] = sorted(data.keys())[:20]
                elif isinstance(data, list):
                    payload["json_type"] = "list"
                    payload["json_size"] = len(data)
            except Exception:
                payload["text_preview"] = (response.text or "")[:200]
            return payload
    except Exception as exc:  # pylint: disable=broad-except
        return {"ok": False, "error": str(exc)}


def probe_post(url: str, json_body: dict[str, Any], timeout: float) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=json_body)
            payload = {
                "ok": True,
                "status_code": response.status_code,
                "content_type": response.headers.get("content-type", ""),
            }
            try:
                data = response.json()
                if isinstance(data, dict):
                    payload["json_keys"] = sorted(data.keys())[:20]
                elif isinstance(data, list):
                    payload["json_type"] = "list"
                    payload["json_size"] = len(data)
            except Exception:
                payload["text_preview"] = (response.text or "")[:200]
            return payload
    except Exception as exc:  # pylint: disable=broad-except
        return {"ok": False, "error": str(exc)}


def probe_gemma_backend(base_url: str, timeout: float) -> dict[str, Any]:
    base = (base_url or "").rstrip("/")
    return {
        "base_url": base,
        "health": probe_get(f"{base}/health", timeout),
        "models": probe_get(f"{base}/v1/models", timeout),
        "props": probe_get(f"{base}/props", timeout),
        "completion": probe_post(
            f"{base}/completion",
            {
                "prompt": "<start_of_turn>user\nhello<end_of_turn>\n<start_of_turn>model\n",
                "n_predict": 1,
                "temperature": 0.0,
            },
            timeout,
        ),
        "chat_completions": probe_post(
            f"{base}/v1/chat/completions",
            {
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 1,
                "temperature": 0.0,
            },
            timeout,
        ),
    }


def probe_asr_openapi(url: str, timeout: float) -> dict[str, Any]:
    try:
        openapi = fetch_json(url, timeout)
        paths = openapi.get("paths", {}) if isinstance(openapi, dict) else {}
        return {
            "ok": True,
            "url": url,
            "path_count": len(paths),
            "paths": sorted(paths.keys()),
            "has_transcriptions": "/v1/audio/transcriptions" in paths,
            "has_translations": "/v1/audio/translations" in paths,
        }
    except Exception as exc:  # pylint: disable=broad-except
        return {
            "ok": False,
            "url": url,
            "error": str(exc),
        }


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv_like(repo_root)

    if args.gemma_url:
        os.environ["GEMMA_TRANSLATE_URL"] = args.gemma_url
    if args.enable_gemma:
        os.environ["GEMMA_TRANSLATE_ENABLED"] = "true"
    if args.translator_services:
        os.environ["TRANSLATOR_SERVICES_LIST"] = args.translator_services

    translator_services = get_translator_services(args)
    engines, translator_warnings = safe_get_translator_capabilities(translator_services)

    if get_bool_env("GEMMA_TRANSLATE_ENABLED", False):
        engines.append(build_gemma_capability_entry())

    runtime_sets = aggregate_runtime_languages(engines)
    engine_summaries = [
        {
            "engine": engine.get("engine"),
            **summarize_engine_languages(engine.get("languages", {})),
        }
        for engine in engines
        if isinstance(engine, dict) and isinstance(engine.get("languages", {}), dict)
    ]

    report = {
        "repo_root": str(repo_root),
        "settings": {
            "TRANSLATOR_SERVICES_LIST": ",".join(translator_services),
            "GEMMA_TRANSLATE_ENABLED": get_bool_env("GEMMA_TRANSLATE_ENABLED", False),
            "GEMMA_TRANSLATE_URL": os.environ.get("GEMMA_TRANSLATE_URL", ""),
            "GEMMA_TRANSLATE_API_MODE": os.environ.get("GEMMA_TRANSLATE_API_MODE", "completion"),
        },
        "translator_warnings": translator_warnings,
        "runtime_aggregation": {
            "source_count": len(runtime_sets["sources"]),
            "target_count": len(runtime_sets["targets"]),
            "supports_vi_as_source": "vi" in runtime_sets["sources"],
            "supports_vi_as_target": "vi" in runtime_sets["targets"],
            "sample_sources": runtime_sets["sources"][:30],
            "sample_targets": runtime_sets["targets"][:30],
        },
        "engine_summaries": engine_summaries,
        "gemma_backend_probe": probe_gemma_backend(args.gemma_url, args.timeout),
        "asr_openapi_probe": probe_asr_openapi(args.asr_openapi_url, args.timeout),
    }

    if args.pretty:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
