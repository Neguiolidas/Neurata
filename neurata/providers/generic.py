"""neurata/providers/generic.py — scanner genérico de diretórios (v1.1).

Um provider só, N formatos. `scan(source_dir, fmt)` anda o diretório
recursivamente, resolve um adapter por arquivo (pelo nome/extensão quando
`fmt="auto"`, ou fixo quando o formato é explícito) e devolve o mesmo par
`(list[Scanned], list[Skipped])` que `claude_code.scan` — por isso o
harvest consome os dois sem ramificar.

Decisões que valem comentário:

* **Adapter recebe `(path, text)`**, não só `path`: o walker já leu os
  bytes pra checar tamanho/binário; reler no adapter seria I/O duplicado
  em fontes de 13k arquivos.
* **Arquivo sem adapter é skip silencioso**, não `Skipped`: varrer um
  repo inteiro com `--format markdown` bateria em milhares de `.png` e
  `.go`; isso é ruído esperado, não falha. Só vira `Skipped` o arquivo
  que o formato reconheceu mas não conseguiu ler (grande, binário, sem
  utf-8, vazio).
* **`os.walk` com poda em `dirnames`**, não `rglob`: `rglob` desce em
  `node_modules`/`.git` inteiro pra depois filtrar. `followlinks=False`
  (default) também mata ciclo de symlink de graça.
* **Ordem determinística** (dirs e arquivos ordenados): dois scans da
  mesma árvore emitem itens na mesma ordem, o que torna o dedup e os
  testes reproduzíveis.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from neurata.providers.claude_code import Skipped

#: 1 MiB — acima disso não é referência de texto, é dump/binário (NFR4).
MAX_FILE_BYTES = 1_048_576

#: Diretórios que nunca contêm referência curada — podados na descida.
EXCLUDE_DIRS = (".git", "__pycache__", "node_modules", ".venv",
                ".mypy_cache", ".ruff_cache", ".pytest_cache")

#: `fmt` → módulo em `neurata.providers.formats`.
_ADAPTER_MODULES = {
    "skill-md": "skill_md",
    "markdown": "markdown",
    "yaml": "yaml",
    "rules": "rules",
}

#: Valores aceitos em `--format`.
FORMATS = ("auto", *_ADAPTER_MODULES)

_RULES_SUFFIXES = (".cursorrules", ".windsurfrules", ".clinerules")

#: Sufixos que cada formato aceita quando pedido EXPLICITAMENTE. Mais
#: largo que `resolve_format`: no modo auto um `SKILL.md` vira `skill-md`
#: (convenção mais específica ganha), mas quem pediu `--format markdown`
#: numa árvore de skills quer os `SKILL.md` também — o inverso, `--format
#: skill-md` sobre `guide.md`, é igualmente válido porque o adapter
#: skill-md cai pro nome do diretório quando não há frontmatter.
_FORMAT_SUFFIXES = {
    "skill-md": (".md", ".markdown"),
    "markdown": (".md", ".markdown"),
    "yaml": (".yaml", ".yml"),
    "rules": _RULES_SUFFIXES,
}


@dataclass(frozen=True)
class Scanned:
    """Item colhido. Campos `name`/`description`/`body`/`source_path`
    são o contrato que `harvest._emit_item` consome — iguais aos de
    `claude_code.Skill`. `fmt` é só diagnóstico."""
    name: str
    description: str
    body: str
    source_path: str
    fmt: str


@dataclass
class WalkConfig:
    root: Path
    fmt: str = "auto"
    max_size: int = MAX_FILE_BYTES
    exclude_dirs: tuple = EXCLUDE_DIRS
    #: subárvores absolutas podadas do walk (ex.: o próprio NEURATA_HOME,
    #: que senão se auto-colhe e duplica a Library a cada rodada).
    exclude_roots: tuple = ()
    #: callable(n, path) chamado por arquivo candidato — R1 (13k YAMLs).
    on_progress: "object | None" = None


def oneline(text: str, limit: int = 200) -> str:
    """Colapsa `text` em uma linha de no máximo `limit` chars.

    Obrigatório, não cosmético: o frontmatter do Neurata é um subset de
    YAML que não representa string multilinha — um `\\n` cru dentro de
    `description:` gera um arquivo que o próprio `frontmatter.parse`
    rejeita depois. Toda string que vai pro meta passa por aqui.
    """
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rstrip()


def accepts(fmt: str, path: Path) -> bool:
    """True se `path` é plausível pro formato `fmt` pedido explicitamente.

    `fmt` já tem que estar resolvido: "auto" não é formato concreto.
    """
    try:
        suffixes = _FORMAT_SUFFIXES[fmt]
    except KeyError:
        raise ValueError(f"formato sem sufixos conhecidos: {fmt!r}") from None
    return path.name.endswith(suffixes)


def resolve_format(path: Path) -> "str | None":
    """Formato inferido do nome do arquivo, ou None se não reconhecido."""
    name = path.name
    if name == "SKILL.md":
        return "skill-md"
    if name.endswith(_RULES_SUFFIXES):
        return "rules"
    if name.endswith((".md", ".markdown")):
        return "markdown"
    if name.endswith((".yaml", ".yml")):
        return "yaml"
    return None


def _adapter(fmt: str):
    """Importa o módulo do adapter sob demanda.

    Lazy porque os adapters importam `Scanned` daqui — import no topo
    seria circular.
    """
    try:
        mod_name = _ADAPTER_MODULES[fmt]
    except KeyError:
        raise ValueError(
            f"formato desconhecido: {fmt!r} "
            f"(use um de: {', '.join(FORMATS)})") from None
    from importlib import import_module
    return import_module(f"neurata.providers.formats.{mod_name}")


def _read_text(path: Path, max_size: int) -> "tuple[str | None, str]":
    """Lê `path` como texto. Devolve `(texto, '')` ou `(None, motivo)`."""
    try:
        size = path.stat().st_size
    except OSError as e:
        return None, f"unreadable: {e}"
    if size > max_size:
        return None, f"too large: {size} bytes > {max_size}"
    try:
        data = path.read_bytes()
    except OSError as e:
        return None, f"unreadable: {e}"
    if b"\x00" in data:
        return None, "binary: null byte"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        return None, f"not utf-8: {e}"
    if not text.strip():
        return None, "empty"
    return text, ""


def _iter_files(cfg: WalkConfig):
    exclude = set(cfg.exclude_dirs)
    # Só paga `resolve()` por diretório quando há subárvore a podar: o caso
    # comum (R1, 13k YAMLs) não regride.
    blocked = tuple(Path(p).resolve() for p in cfg.exclude_roots)

    def _is_blocked(path: Path) -> bool:
        try:
            real = path.resolve()
        except OSError:
            return False
        return any(real == b or real.is_relative_to(b) for b in blocked)

    if blocked and _is_blocked(Path(cfg.root)):
        return
    for dirpath, dirnames, filenames in os.walk(cfg.root):
        dirnames[:] = sorted(d for d in dirnames if d not in exclude)
        if blocked:
            dirnames[:] = [d for d in dirnames
                           if not _is_blocked(Path(dirpath) / d)]
        for fname in sorted(filenames):
            yield Path(dirpath) / fname


def walk(cfg: WalkConfig) -> "tuple[list[Scanned], list[Skipped]]":
    """Anda `cfg.root` e devolve os itens colhidos + os pulados com motivo."""
    # Erro de chamador antes de qualquer early-return: raiz ausente não pode
    # mascarar formato inválido (o mesmo cfg errado falharia só às vezes).
    if cfg.fmt not in FORMATS:
        raise ValueError(
            f"formato desconhecido: {cfg.fmt!r} "
            f"(use um de: {', '.join(FORMATS)})")
    scanned: list[Scanned] = []
    skipped: list[Skipped] = []
    if not cfg.root.is_dir():
        return scanned, skipped

    fixed = None if cfg.fmt == "auto" else cfg.fmt
    seen = 0
    for path in _iter_files(cfg):
        if fixed is None:
            fmt = resolve_format(path)
            if fmt is None:
                continue
        else:
            # Formato fixo ainda não trata .png como markdown: a extensão
            # tem que ser plausível pro formato pedido.
            if not accepts(fixed, path):
                continue
            fmt = fixed
        seen += 1
        if callable(cfg.on_progress):
            cfg.on_progress(seen, path)
        text, reason = _read_text(path, cfg.max_size)
        if text is None:
            skipped.append(Skipped(str(path), reason))
            continue
        try:
            item = _adapter(fmt).parse(path, text)
        # Adapter de terceiro/formato torto não pode derrubar um batch de
        # 13k arquivos (NFR3). O motivo fica no report.
        except Exception as e:  # noqa: BLE001
            skipped.append(Skipped(str(path), f"{fmt}: {e}"))
            continue
        if item is None:
            skipped.append(Skipped(str(path), f"{fmt}: não reconhecido"))
            continue
        scanned.append(item)
    return scanned, skipped


def scan(source_dir: Path, fmt: str = "auto",
         **kwargs) -> "tuple[list[Scanned], list[Skipped]]":
    """Contrato de provider: mesma assinatura efetiva de `claude_code.scan`."""
    return walk(WalkConfig(root=Path(source_dir), fmt=fmt, **kwargs))
