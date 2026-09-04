from __future__ import annotations

from pathlib import Path
from typing import Dict

from trading_app.config_schema import default_settings_map


def load_env_file(env_path: Path) -> Dict[str, str]:
    values = default_settings_map()
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def save_env_file(env_path: Path, values: Dict[str, str]) -> None:
    lines = []
    for key in sorted(values):
        value = values[key].strip()
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
