"""Best-effort lookup in the public game store catalog."""
import json
import re
import time
import urllib.request
from pathlib import Path
from ..i18n import tr  # noqa: E402

URL = "https://raw.githubusercontent.com/Ephellon/game-store-catalog/main/ps5/!.json"
TTL = 7 * 24 * 3600


def cache_path():
    return Path.home() / ".cache" / "ps5-converter" / "catalog.json"


def parse_catalog(data):
    result = {}
    for item in data:
        if not isinstance(item, list) or len(item) < 2 or not isinstance(item[1], dict):
            continue
        value = item[1]
        code = re.search(r"PPSA\d{5}", str(value.get("uuid", "")))
        name = value.get("name") or item[0]
        if code and isinstance(name, str) and name.strip() and code.group() not in result:
            result[code.group()] = name.strip()
    return result


def lookup(code):
    """Never prevent conversion if offline or catalog is malformed."""
    cache = cache_path()
    try:
        if cache.exists() and time.time() - cache.stat().st_mtime < TTL:
            return json.loads(cache.read_text()).get(code)
    except (OSError, ValueError):
        pass
    try:
        with urllib.request.urlopen(URL, timeout=4) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError(tr("Catálogo excessivamente grande"))
        mapping = parse_catalog(json.loads(raw))
        if mapping:
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".tmp")
            tmp.write_text(json.dumps(mapping, ensure_ascii=False))
            tmp.replace(cache)
            return mapping.get(code)
    except (OSError, ValueError, TimeoutError):
        pass
    try:
        return json.loads(cache.read_text()).get(code)
    except (OSError, ValueError):
        return None
