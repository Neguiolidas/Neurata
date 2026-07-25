"""neurata/config.py — config de query: defaults + validação estrita.

Ausente → defaults, zero I/O extra. Malformada OU chave desconhecida →
ConfigError com remediação (typo silenciosamente ignorado seria degradação
silenciosa, proibida por princípio do projeto).
"""
import copy
import json

from neurata.home import NeurataHome

DEFAULTS: dict = {
    "bm25_weights": {"title": 4.0, "aliases": 3.0, "tags": 2.0, "body": 1.0},
    "variant_weights": {"raw": 1.0, "norm": 0.8, "singular": 0.6,
                        "plural": 0.6, "prefix": 0.5},
    "rrf_k": 60,
    "w_ppr": 0.3,
    "skill_boost": 1.5,
    "shelf": {"w_u": 1.0, "w_r": 1.0, "w_c": 0.5, "tau_dias": 30.0,
              "beta": 0.15},
    "snapshot": {"remote": None, "auto_push": False},
}
# schema_version pertence ao config desde a fase 1 (home.init escreve).
_TOP_KEYS = set(DEFAULTS) | {"schema_version"}
_REMEDY = "corrija ou remova a chave em config.json (typo?)"
# snapshot é subtree tipado (não numérico) — validado à parte, sem passar
# por _require_number. bool é checado antes por isinstance direto: como o
# tipo esperado de auto_push é `bool` (não `int`), isinstance(1, bool) já
# retorna False, então não há o overlap bool/int que _require_number trata.
_SNAPSHOT_TYPES = {"auto_push": bool, "remote": (str, type(None))}


class ConfigError(ValueError):
    pass


def load(home: NeurataHome) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    if not home.config_path.exists():
        return cfg
    try:
        raw = json.loads(home.config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(
            f"config.json malformado ({exc}) — restaure ou apague o arquivo "
            "(sem ele, defaults assumem)") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config.json deve ser objeto JSON — {_REMEDY}")
    unknown = sorted(set(raw) - _TOP_KEYS)
    if unknown:
        raise ConfigError(
            f"chave(s) desconhecida(s) em config.json: {unknown} — {_REMEDY}")
    for key, val in raw.items():
        if key == "schema_version":
            continue
        default = DEFAULTS[key]
        if key == "snapshot":
            if not isinstance(val, dict):
                raise ConfigError(
                    f"config.json: {key} deve ser objeto — {_REMEDY}")
            sub_unknown = sorted(set(val) - set(default))
            if sub_unknown:
                raise ConfigError(
                    f"chave(s) desconhecida(s) em config.json "
                    f"{key}: {sub_unknown} — {_REMEDY}")
            for sk, sv in val.items():
                expected = _SNAPSHOT_TYPES[sk]
                if not isinstance(sv, expected):
                    raise ConfigError(
                        f"config.json: snapshot.{sk} tipo inválido — "
                        f"{_REMEDY}")
            cfg[key].update(val)
        elif isinstance(default, dict):
            if not isinstance(val, dict):
                raise ConfigError(
                    f"config.json: {key} deve ser objeto — {_REMEDY}")
            sub_unknown = sorted(set(val) - set(default))
            if sub_unknown:
                raise ConfigError(
                    f"chave(s) desconhecida(s) em config.json "
                    f"{key}: {sub_unknown} — {_REMEDY}")
            for sk, sv in val.items():
                _require_number(f"{key}.{sk}", sv)
            cfg[key].update(val)
        else:
            _require_number(key, val)
            cfg[key] = val
    return cfg


def _require_number(where: str, v: object) -> None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ConfigError(
            f"config.json: {where} deve ser numérico — {_REMEDY}")
