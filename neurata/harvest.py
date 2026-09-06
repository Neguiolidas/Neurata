"""neurata/harvest.py — orquestrador: providers externos → itens no inbox.

`harvest(home, target)` resolve o `skills_dir` da fonte (env
`NEURATA_CLAUDE_SKILLS_DIR` ou `~/.claude/skills` p/ "claude-code"),
chama `provider.scan`, compara contra o que já está na library
(`source_key`) e contra o que já está pendente no inbox (evita
duplicar), e emite itens novos/atualizados + tombstones pro que sumiu
da fonte. Read-only no índice: nenhum INSERT/UPDATE em `entries` —
harvest só escreve `.md` no inbox; quem indexa é o `tick`/`reindex`.
"""
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path, PurePath

from neurata.frontmatter import FrontmatterError, parse, serialize
from neurata.home import NeurataHome
from neurata.indexdb import check_schema, connect, migrate_if_needed
from neurata.providers import GENERIC, REGISTRY, resolve
from neurata.providers.formats import FORMAT_CLASS
from neurata.ulid import new_ulid

__all__ = ["GENERIC", "REGISTRY", "HarvestReport", "harvest", "validate_target"]

_DEFAULT_SKILLS_DIR_ENV = "NEURATA_CLAUDE_SKILLS_DIR"
_DEFAULT_SKILLS_DIR = "~/.claude/skills"


@dataclass
class HarvestReport:
    target: str
    harvested: int = 0
    updated: int = 0
    removed: int = 0
    skipped: list = field(default_factory=list)


def _default_skills_dir() -> Path:
    raw = os.environ.get(_DEFAULT_SKILLS_DIR_ENV) or _DEFAULT_SKILLS_DIR
    return Path(raw).expanduser()


def validate_target(target: str) -> None:
    """Rejeita target que quebre o namespace `<target>:<algo>`.

    `:` é o separador: com ele no nome, o prefixo de um alvo casa as
    chaves de outro (`a:` casa `a:b:x.md`) e o scan de `a` emite
    tombstone nos itens de `a:b`. Alvo vazio gera chaves `:algo`.
    """
    if not target.strip():
        raise ValueError("target vazio")
    if ":" in target:
        raise ValueError(f"':' é o separador de source_key, "
                         f"não pode aparecer no target: {target!r}")


def _like_prefix(namespace: str) -> str:
    """Pattern LIKE que casa `<namespace>:*` literalmente (usar ESCAPE '\\').

    O namespace carrega nome de diretório do usuário, e `_`/`%` são
    wildcards em LIKE — sem escapar, colher `foo_bar` acharia `fooXbar`.
    """
    escaped = (namespace.replace("\\", r"\\")
                        .replace("%", r"\%")
                        .replace("_", r"\_"))
    return f"{escaped}:%"


def _namespace(target: str, source_dir: "Path | None") -> str:
    """Prefixo dos source_keys desta colheita — o que o harvest "possui".

    É por ele que o harvest filtra o que já conhece (query em `entries`,
    varredura do inbox) e decide quem virou tombstone. Dois scans que
    compartilham namespace se canibalizam: o de um emite tombstone nos
    itens do outro.

    Provider nomeado: só o `target` (compat com o que já está indexado).
    Provider genérico: `target` + hash do diretório resolvido. O target
    default é o basename do path, então `a/sub` e `b/sub` cairiam no
    mesmo namespace sendo fontes distintas; o hash desempata. Resolver o
    path antes faz dois caminhos equivalentes (symlink, `..`) baterem no
    mesmo namespace, que é o que se espera de recolher a mesma fonte.
    """
    if source_dir is None:
        return target
    real = source_dir.resolve().as_posix()
    digest = hashlib.sha256(real.encode("utf-8")).hexdigest()[:12]
    return f"{target}@{digest}"


def _source_key_fn(namespace: str, source_dir: "Path | None"):
    """Como um item colhido vira `source_key` — `<namespace>:<algo>`.

    Provider nomeado usa `skill.name` (compat com o que já está indexado);
    provider genérico usa o caminho relativo à raiz, que é único dentro
    da árvore e sobrevive a renomear título.
    """
    if source_dir is None:
        # Provider pode declarar a chave do item (o `project` usa o
        # caminho relativo — renomear título não troca identidade);
        # sem `key`, mantém o padrão histórico por nome.
        return lambda item: (f"{namespace}:"
                             f"{getattr(item, 'key', None) or item.name}")

    def key(item) -> str:
        rel = os.path.relpath(item.source_path, source_dir)
        return f"{namespace}:{PurePath(rel).as_posix()}"
    return key


def harvest(home: NeurataHome, target: str,
           skills_dir: "Path | None" = None,
           source_dir: "Path | None" = None,
           fmt: str = "auto") -> HarvestReport:
    """Colhe `target` pro inbox. `source_dir` liga o provider genérico.

    Sem `source_dir`: provider nomeado do REGISTRY (`claude-code`), que
    lê `skills_dir`. Com `source_dir`: anda aquele diretório com o
    adapter `fmt` e `target` é só o rótulo/namespace dos source_keys.
    """
    validate_target(target)
    ns_fn = None
    if source_dir is not None:
        provider = resolve(GENERIC)
        source_dir = Path(source_dir).expanduser()
        # O walker trata raiz ausente como "zero arquivos", o que aqui seria
        # destrutivo: scan vazio + entries conhecidas = tombstone em tudo.
        # Diretório sumido (não-montado, typo) é erro do chamador, não uma
        # colheita vazia legítima.
        if not source_dir.is_dir():
            raise ValueError(f"diretório inexistente: {source_dir}")
        # Colher de dentro do próprio home re-ingere a Library: cada rodada
        # dobra o acervo (1→2→4→8...), com id/source_key novos por clone.
        # Raiz dentro do home é erro do chamador; home dentro da raiz é
        # legítimo (`harvest ~/`) e só perde a subárvore do home.
        real_home, real_src = home.root.resolve(), source_dir.resolve()
        if real_src == real_home or real_src.is_relative_to(real_home):
            raise ValueError(
                f"origem dentro do NEURATA_HOME ({real_home}): colher a "
                f"própria Library duplicaria o acervo a cada rodada")
        scan_args = ((source_dir,), {"fmt": fmt,
                                     "exclude_roots": (real_home,)})
    else:
        provider = resolve(target)
        if skills_dir is None:
            # Provider nomeado pode ter local default próprio (o
            # `project` colhe a raiz git do cwd; claude-code mantém o
            # dir global de skills).
            skills_dir = getattr(provider, "default_dir",
                                 _default_skills_dir)()
        # Guard do self-ingest para provider ROOT-SCOPED (que "possui"
        # a árvore inteira da raiz, como o genérico): raiz dentro do
        # NEURATA_HOME duplicaria o acervo a cada rodada. O claude-code
        # escaneia um padrão estreito (SKILL.md) e fica fora.
        ns_fn = getattr(provider, "namespace", None)
        if ns_fn is not None and skills_dir is not None:
            real_root = Path(skills_dir).resolve()
            real_home = home.root.resolve()
            if real_root == real_home or real_root.is_relative_to(real_home):
                raise ValueError(
                    f"origem dentro do NEURATA_HOME ({real_home}): colher a "
                    f"própria Library duplicaria o acervo a cada rodada")
        scan_args = ((skills_dir,), {})
    namespace = _namespace(target, source_dir)
    if source_dir is None and ns_fn is not None:
        # Provider root-scoped: o que ele "possui" é a raiz resolvida
        # (mesmo desenho do genérico) — colher o mesmo repo
        # re-sincroniza; repos distintos não colidem.
        namespace = ns_fn(Path(skills_dir) if skills_dir else None)
    source_key_of = _source_key_fn(namespace, source_dir)

    con = connect(home)
    try:
        # Harvest não detém o lock aqui; `LockHeldError` sobe e a CLI já
        # a traduz (mesmo tratamento que qualquer escrita concorrente).
        migrate_if_needed(con, home)
        check_schema(con, require_reindexed=False)

        known: dict = {}
        known_paths: dict = {}
        # Skip composto (v1.8): (content_hash, class, tags do índice) —
        # comparar só o hash do corpo deixaria a re-colheita pós-1.8
        # consumir itens cujos tags/class a fonte agora declara (o
        # buraco que manteve entry_tags=0 por quatro releases).
        for sk, chash, path_str, cls in con.execute(
                "SELECT source_key, content_hash, path, class FROM entries"
                " WHERE source_key LIKE ? ESCAPE '\\'"
                " AND location='library'",
                (_like_prefix(namespace),)).fetchall():
            known[sk] = (chash, cls, ())
            known_paths[sk] = path_str
        # tags no índice já são lower-case (o writer normaliza)
        for sk, tag in con.execute(
                "SELECT e.source_key, t.tag FROM entry_tags t"
                " JOIN entries e ON e.rowid = t.entry_rowid"
                " WHERE e.source_key LIKE ? ESCAPE '\\'"
                " AND e.location='library'",
                (_like_prefix(namespace),)).fetchall():
            if sk in known:
                chash, cls, tags = known[sk]
                known[sk] = (chash, cls, (*tags, tag))
    finally:
        con.close()

    pending, pending_tombstones = _scan_inbox_pending(home, namespace)

    args, kwargs = scan_args
    skills, skipped = provider.scan(*args, **kwargs)
    report = HarvestReport(target=target, skipped=list(skipped))

    scanned_keys: set = set()
    for skill in skills:
        source_key = source_key_of(skill)
        scanned_keys.add(source_key)
        body_hash = hashlib.sha256(skill.body.encode("utf-8")).hexdigest()
        # A emissão esperada é o triplo inteiro — corpo, classe da forma
        # e tags — porque o skip tem que divergir quando QUALQUER campo
        # muda na fonte, não só o corpo.
        memory_class = FORMAT_CLASS.get(getattr(skill, "fmt", ""))
        skill_tags = tuple(sorted(t.lower()
                                  for t in getattr(skill, "tags", [])))
        emission = (body_hash, memory_class, skill_tags)
        if known.get(source_key) == emission:
            continue
        if pending.get(source_key) == emission:
            continue
        _emit_item(home, target, skill, source_key, body_hash)
        if source_key in known:
            report.updated += 1
        else:
            report.harvested += 1

    removed_keys = (set(known) | set(pending)) - scanned_keys
    for source_key in sorted(removed_keys):
        if source_key in pending_tombstones:
            continue
        if _is_already_stale(home, known_paths.get(source_key)):
            continue
        _emit_tombstone(home, source_key)
        report.removed += 1

    return report


def _is_already_stale(home: NeurataHome, rel_path: "str | None") -> bool:
    """True se a entry da library em `rel_path` já está marcada stale.

    Evita reemitir tombstone (e inflar `report.removed`) pra uma entry que
    um tick anterior já processou como stale — o skill sumiu da fonte uma
    vez, o tombstone rodou, e nada mudou desde então. `rel_path` é None
    quando o source_key só existe em `pending` (item ainda não indexado);
    nesse caso não há arquivo de library pra checar.
    """
    if not rel_path:
        return False
    path = home.root / rel_path
    try:
        text = path.read_text(encoding="utf-8")
        meta, _body = parse(text)
    except (OSError, UnicodeDecodeError, FrontmatterError):
        return False
    return str(meta.get("stale", "")).lower() == "true"


def _scan_inbox_pending(home: NeurataHome,
                        namespace: str) -> "tuple[dict, set]":
    prefix = f"{namespace}:"
    pending: dict = {}
    pending_tombstones: set = set()
    if not home.inbox.is_dir():
        return pending, pending_tombstones
    for path in sorted(home.inbox.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
            meta, body = parse(text)
        except (OSError, UnicodeDecodeError, FrontmatterError):
            continue
        source_key = meta.get("source_key")
        if not source_key or not str(source_key).startswith(prefix):
            continue
        source_key = str(source_key)
        if meta.get("type") == "skill-tombstone":
            pending_tombstones.add(source_key)
        else:
            content_hash = meta.get("content_hash") or hashlib.sha256(
                body.encode("utf-8")).hexdigest()
            # mesmo triplo do `known`: (hash, class, tags lower-case)
            raw_tags = meta.get("tags") or []
            if isinstance(raw_tags, str):
                raw_tags = [t.strip() for t in raw_tags.split(",")
                            if t.strip()]
            tags = tuple(sorted(str(t).lower() for t in raw_tags))
            pending[source_key] = (content_hash, meta.get("class"), tags)
    return pending, pending_tombstones


def _emit_item(home: NeurataHome, target: str, skill, source_key: str,
               body_hash: str) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry_id = new_ulid()
    meta = {
        "id": entry_id,
        "type": "skill",
        "env": target,
        "title": skill.name,
        "description": skill.description,
        "source_key": source_key,
        "source_path": skill.source_path,
        "created": now,
        "content_hash": body_hash,
    }
    # Tags/aliases (v1.8): o que a FONTE declara vai ao frontmatter do
    # espelho — é ali que o tick (e o reindex) os transformam em
    # entry_tags e coluna FTS de peso 2.0. Fonte que não declara não
    # escreve chave nenhuma.
    skill_tags = getattr(skill, "tags", None)
    if skill_tags:
        meta["tags"] = [str(t) for t in skill_tags]
    skill_aliases = getattr(skill, "aliases", None)
    if skill_aliases:
        meta["aliases"] = [str(a) for a in skill_aliases]
    # A classe do espelho é declarada pela forma que o adapter leu, e o
    # frontmatter do espelho é onde ela fica auditável. Item sem `fmt`
    # (provider de fora da árvore) ou formato fora do mapa não vira
    # palpite: nasce sem `class:` e cai em `missing:class`.
    memory_class = FORMAT_CLASS.get(getattr(skill, "fmt", ""))
    if memory_class:
        meta["class"] = memory_class
    from neurata.textnorm import slugify
    slug = slugify(skill.name)
    path = home.inbox / f"{entry_id}-{slug}.md"
    path.write_text(serialize(meta, skill.body), encoding="utf-8")


def _emit_tombstone(home: NeurataHome, source_key: str) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry_id = new_ulid()
    meta = {
        "id": entry_id,
        "type": "skill-tombstone",
        "source_key": source_key,
        "created": now,
    }
    slug = source_key.replace(":", "-").replace("/", "-")
    path = home.inbox / f"{entry_id}-{slug}-tombstone.md"
    path.write_text(serialize(meta, ""), encoding="utf-8")
