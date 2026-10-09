"""의존성 없는 간단한 YAML 직렬화 (VIEW 결과를 사람/AI 친화적으로 출력)."""
from __future__ import annotations
import json
import re

_PLAIN = re.compile(r"^[A-Za-z_][A-Za-z0-9_./\- ]*$")


def _scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return json.dumps(v)
    s = str(v)
    if "\n" in s or not _PLAIN.match(s) or s.lower() in ("null", "true", "false", "yes", "no") or s != s.strip():
        return json.dumps(s, ensure_ascii=False)
    return s


def dump(obj, indent: int = 0) -> str:
    pad = "  " * indent
    lines: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                if isinstance(v, list) and all(not isinstance(x, (dict, list)) for x in v) and len(json.dumps(v)) < 100:
                    lines.append(f"{pad}{k}: {json.dumps(v, ensure_ascii=False)}")
                else:
                    lines.append(f"{pad}{k}:")
                    lines.append(dump(v, indent + 1))
            elif isinstance(v, (dict, list)):
                lines.append(f"{pad}{k}: {'{}' if isinstance(v, dict) else '[]'}")
            else:
                lines.append(f"{pad}{k}: {_scalar(v)}")
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict) and item:
                sub = dump(item, indent + 1).split("\n")
                first = sub[0].lstrip()
                lines.append(f"{pad}- {first}")
                lines.extend(sub[1:])
            elif isinstance(item, (dict, list)):
                lines.append(f"{pad}- {json.dumps(item, ensure_ascii=False)}")
            else:
                lines.append(f"{pad}- {_scalar(item)}")
    else:
        lines.append(f"{pad}{_scalar(obj)}")
    return "\n".join(lines)
