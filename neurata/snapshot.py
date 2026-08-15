"""neurata/snapshot.py — git audit da library (plumbing best-effort).

Espelha o padrão de `envelope.py`: subprocess puro (sem `import git`/
dulwich), timeout, catch `(OSError, subprocess.TimeoutExpired)`. Repo git
vive dentro de `home.library` (repo root = library dir); `git -C
<library>` sempre, nunca `chdir`.

NUNCA importar `neurata.tick` aqui (ciclo: tick.py importa `commit_tick`
deste módulo). Funções que recebem um `TickReport` fazem duck-typing —
só acessam atributos (`report.processed` etc.), nunca `isinstance`.
"""
import subprocess

from neurata.home import CONTRACT_VERSION, SCHEMA_VERSION
from neurata.indexdb import INDEX_SCHEMA_VERSION, IndexLock, connect

# Import top-level (não local): checado que `neurata.reindex` NÃO importa
# `neurata.snapshot` nem `neurata.tick` — sem ciclo. Diferente do caso
# `commit_tick`/`tick.py` (esse sim circular; daí o duck-typing citado
# acima). `_reindex_locked` já é desenhado pra ser chamado por um caller
# externo (reindex() ou restore()) que detém lock/conexão — ver docstring
# dele em reindex.py.
from neurata.reindex import _reindex_locked

GIT_TIMEOUT = 15  # commit/checkout local; push tem timeout próprio maior
PUSH_TIMEOUT = 60  # rede: mais generoso que operações locais

_AVAIL: "bool | None" = None

# Ordem fixa do subject (spec §3): sinal + label. `stale` não tem sinal —
# nunca aparece no subject, só no body.
_SUBJECT_CATEGORIES = (
    ("processed", "+", "catalogados"),
    ("updated", "~", "atualizado"),
    ("absorbed", "↻", "absorvido"),
    ("quarantined", "-", "quarentena"),
    ("conflicts", "⚠", "dup"),
    ("renamed", "→", "rename"),
)

# Ordem fixa do body (spec §3): sempre as 7 linhas, mesmo com valor 0.
_BODY_LABELS = (
    ("cataloga:", "processed"),
    ("atualiza:", "updated"),
    ("absorve:", "absorbed"),
    ("quarentena:", "quarantined"),
    ("near-dup:", "conflicts"),
    ("rename:", "renamed"),
    ("stale:", "stale"),
)

_BODY_LABEL_WIDTH = 13  # "quarentena:" (11) + 2 espaços = maior label + margem


def _body_extra(attr: str, report) -> "str | None":
    """Glosa entre parênteses de cada linha do body — só quando o
    contador correspondente é não-zero. `processed` é o único caso cujo
    detalhe é um contador de fato (report.literate); os demais são texto
    fixo descrevendo a categoria (não há subtipo no TickReport)."""
    if attr == "processed":
        literate = getattr(report, "literate", 0)
        return f"{literate} alfabetizados" if literate else None
    if attr == "updated":
        return "source-keyed in-place" if report.updated else None
    if attr == "quarantined":
        return "duplicata exata" if report.quarantined else None
    if attr == "conflicts":
        return "marcado conflito" if report.conflicts else None
    return None


def _tick_subject(report) -> str:
    """Subject (≤72 col) — spec §3. Só categorias não-zero, ordem fixa
    `+catalogados ~atualizado -quarentena ⚠dup →rename`. Se nenhuma for
    não-zero (ex.: edição aditiva de `conflicts_with` sem novo processed/
    updated/quarantined/conflicts/renamed), cai no fallback genérico."""
    parts = [f"{sign}{value} {label}"
            for attr, sign, label in _SUBJECT_CATEGORIES
            if (value := getattr(report, attr, 0))]
    if not parts:
        return "snapshot: metadados atualizados"
    return "snapshot: " + ", ".join(parts)


def _tick_body(report) -> str:
    """Body (spec §3) — detalhamento por tipo de op, sempre as 6
    categorias (mesmo zeradas), rodapé com tick id + versões de schema."""
    lines = []
    for label, attr in _BODY_LABELS:
        value = getattr(report, attr, 0)
        line = f"{label:<{_BODY_LABEL_WIDTH}}{value}"
        extra = _body_extra(attr, report)
        if extra:
            line += f"  ({extra})"
        lines.append(line)
    lines.append("")
    lines.append(f"tick: {report.tick}")
    lines.append(f"schema: config={SCHEMA_VERSION} index={INDEX_SCHEMA_VERSION}"
                f" contract={CONTRACT_VERSION}")
    return "\n".join(lines)


class SnapshotError(RuntimeError):
    pass


def _run(home, *args, timeout=GIT_TIMEOUT, check=False) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(home.library), *args]
    try:
        # check=False literal: quem decide sobre returncode é o `check`
        # DESTA função, logo abaixo — o subprocess nunca levanta sozinho.
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        if check:
            raise SnapshotError(str(exc)) from exc
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr=str(exc))
    if check and proc.returncode != 0:
        raise SnapshotError(proc.stderr)
    return proc


def git_available() -> bool:
    # _AVAIL é memo de processo: "git existe?" não muda durante uma
    # execução, e cada tick chamaria isso dezenas de vezes. O global É a
    # feature — sem ele, um subprocess por chamada.
    global _AVAIL  # noqa: PLW0603
    if _AVAIL is None:
        try:
            proc = subprocess.run(["git", "--version"], capture_output=True,
                                   text=True, timeout=GIT_TIMEOUT, check=False)
        except (OSError, subprocess.TimeoutExpired):
            _AVAIL = False
        else:
            _AVAIL = proc.returncode == 0
    return _AVAIL


def ensure_repo(home) -> bool:
    if not git_available():
        return False
    if (home.library / ".git").exists():
        return True
    _run(home, "init", "-q", "-b", "main")
    _run(home, "config", "user.name", "neurata")
    _run(home, "config", "user.email", "neurata@localhost")
    _run(home, "config", "commit.gpgsign", "false")
    _run(home, "config", "core.autocrlf", "false")
    _run(home, "config", "core.hooksPath", "/dev/null")
    # Return estado REAL: se init falhou (lib ausente/permissão/disco) o
    # .git não existe — não mentir "pronto" (senão commit degrada a None
    # silencioso sem log). best-effort honesto.
    return (home.library / ".git").exists()


def has_changes(home) -> bool:
    if not git_available():
        return False
    proc = _run(home, "status", "--porcelain")
    return bool(proc.stdout.strip())


def commit(home, subject, body="") -> "str | None":
    if not git_available():
        return None
    _run(home, "add", "-A")
    if not has_changes(home):
        return None
    args = ["commit", "-m", subject]
    if body:
        args += ["-m", body]
    _run(home, *args, check=True)
    proc = _run(home, "rev-parse", "--short=12", "HEAD", check=True)
    _gc_auto(home)
    return proc.stdout.strip()


def _gc_auto(home) -> None:
    """Poda a trilha, não só o acervo: cada commit deixa um objeto solto
    por arquivo tocado, e uma compactação em lote toca centenas. Sem isso
    o .git cresce mais do que a compactação economiza (medido: +1,69 MiB
    de objetos soltos contra −1,65 MiB de corpo, em 1004 grãos; `gc`
    empacota os mesmos objetos em metade do tamanho).

    `--auto` é no-op barato abaixo do limiar do próprio git (gc.auto), e
    roda destacado acima dele — nunca segura o tick. Best-effort: falha de
    gc não invalida um commit que já está feito.
    """
    _run(home, "gc", "--auto", "--quiet")


def commit_tick(home, report) -> "str | None":
    """Commit best-effort do tick — spec §3+§4. `report` é um TickReport
    (duck-typed, ver módulo docstring). Chama `has_changes` primeiro:
    tree limpo → `None`, sem commit, mesmo que os contadores do report
    sejam >0 (nada a versionar). Tree sujo → sempre commita, mesmo com
    report todo-zero (ex.: edição aditiva de `conflicts_with`), usando o
    subject fallback de `_tick_subject`."""
    if not has_changes(home):
        return None
    return commit(home, _tick_subject(report), _tick_body(report))


def list_snapshots(home, limit: int = 20) -> "list[dict]":
    """`git log` em `[{sha, ts, subject}]` (spec §7.2) — best-effort: sem
    git ou repo vazio/inexistente devolve lista vazia, nunca levanta."""
    if not git_available():
        return []
    proc = _run(home, "log", f"-n{limit}",
               "--pretty=format:%h\x1f%cI\x1f%s")
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    out = []
    for line in proc.stdout.splitlines():
        if not line:
            continue
        sha, ts, subject = line.split("\x1f", 2)
        out.append({"sha": sha, "ts": ts, "subject": subject})
    return out


def commit_manual(home) -> dict:
    """Commit manual do estado atual da library (spec §7.1), sob
    `IndexLock` — mesmo lock que tick/restore usam, pra serializar contra
    um tick concorrente (dois `git` na mesma library colidiriam no
    `index.lock` do próprio git; o `IndexLock` do Neurata é a barreira de
    verdade — spec §9). Subject `snapshot: manual (N arquivos)`, body =
    `git diff --cached --stat` (spec §3). Sem git/sem mudança → no-op
    silencioso (`snapshot: None, changed: False`), nunca erro (§2.1: só
    `--push` explícito exige falha alta)."""
    with IndexLock(home):
        ensure_repo(home)
        _run(home, "add", "-A")
        names = _run(home, "diff", "--cached",
                    "--name-only").stdout.splitlines()
        n = len(names)
        if n == 0:
            return {"ok": True, "snapshot": None, "changed": False}
        stat = _run(home, "diff", "--cached", "--stat").stdout.rstrip("\n")
        sha = commit(home, f"snapshot: manual ({n} arquivos)", stat)
        return {"ok": True, "snapshot": sha, "changed": sha is not None}


def restore(home, ref: str) -> dict:
    """Restore atômico (spec §5) — materializa o tree de `ref` como um
    NOVO commit em `main` (forward-only; history nunca reescrita) e
    reindexa sob o MESMO `IndexLock` (um único lock pro método inteiro —
    nunca reabre).

    1. Valida `ref` via `rev-parse --verify <ref>^{commit}` (check=True)
       — ref inexistente propaga `SnapshotError` ANTES de qualquer
       mutação (tree e HEAD intocados).
    2. Autosave: dirty pré-restore vira commit real em `main`
       (recuperável por sha/reflog) — nunca perde estado sujo. `commit()`
       já é no-op (`None`) se o tree estiver limpo.
    3. Materializa o tree do ref **exatamente** via `read-tree -u
       --reset` (índice + working tree viram o tree do ref, deletando
       arquivos ausentes nele) — plumbing ancestral, sem fallback de
       versão. NUNCA `checkout <ref> -- .` / `restore --source`: esses só
       tocam paths que casam no estado atual e não deletam um arquivo
       adicionado depois do ref (tree resultante ficaria != tree do ref).
       Guard: se o tree do ref já é igual ao HEAD atual (`diff --quiet`)
       não há nada a materializar — pula read-tree/commit,
       `new_head=HEAD`.
    4. Reindex sob o MESMO lock/conexão via `_reindex_locked` (nunca
       reabre `IndexLock` nem chama `reindex()` público). Falha aqui é
       capturada — o tree já está consistente em disco, só o índice
       fica stale — retorna `ok:False` com `need:"reindex"`, sem
       propagar (exceção nunca vaza meio-caminho).
    """
    with IndexLock(home):
        ensure_repo(home)
        _run(home, "rev-parse", "--verify", f"{ref}^{{commit}}", check=True)

        autosaved = commit(home, "snapshot: autosave antes de restore")

        if _run(home, "diff", "--quiet", ref, "HEAD").returncode == 0:
            new_head = _run(home, "rev-parse", "HEAD",
                            check=True).stdout.strip()
        else:
            _run(home, "read-tree", "-u", "--reset", ref, check=True)
            short_ref = _run(home, "rev-parse", "--short=12", ref,
                             check=True).stdout.strip()
            commit(home, f"snapshot: restore para {short_ref}")
            new_head = _run(home, "rev-parse", "HEAD",
                            check=True).stdout.strip()

        con = connect(home)
        try:
            rx = _reindex_locked(home, con)
        # O restore do git já aconteceu. Qualquer falha do reindex tem que
        # virar resultado estruturado ("need": "reindex"), nunca exceção —
        # senão o chamador não sabe que a library mudou.
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "restored_to": ref, "autosaved": autosaved,
                    "new_head": new_head, "need": "reindex",
                    "reindex_error": str(exc)}
        finally:
            con.close()

        return {"ok": True, "restored_to": ref, "autosaved": autosaved,
                "new_head": new_head, "reindex": rx}


def restore_dry_run(home, ref: str) -> dict:
    """Preview de `restore` (spec §7.3) sem `--yes` — NUNCA muta HEAD nem
    working tree. `ref` inválido → `{"ok": False, "error": ...}` (sem
    tocar nada, igual ao `restore` real); ref válido → resumo do que o
    restore de verdade faria: `diff --stat HEAD..<ref>` e se há dirty
    (que viraria autosave)."""
    ensure_repo(home)
    verify = _run(home, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if verify.returncode != 0:
        return {"ok": False, "restored_to": ref,
                "error": f"ref inválido: {ref}"}
    diff_stat = _run(home, "diff", "--stat",
                     f"HEAD..{ref}").stdout.rstrip("\n")
    return {"ok": False, "restored_to": ref, "dry_run": True,
            "would_change": diff_stat, "dirty": has_changes(home)}


def set_remote(home, url: str) -> None:
    proc = _run(home, "remote", "get-url", "neurata")
    if proc.returncode == 0:
        _run(home, "remote", "set-url", "neurata", url)
    else:
        _run(home, "remote", "add", "neurata", url)


def push(home, *, remote_url: "str | None" = None) -> dict:
    """Push best-effort pro remote `neurata` (spec §7.4/§7.5). Nunca
    levanta — falha vira `{"ok": False, ..., "error": ...}` (o caller CLI
    decide o exit code; o caminho auto do tick só loga). `remote_url`,
    quando passado, sincroniza o remote git com o config ANTES do push
    (cobre o caso do remote git ter sumido — ex.: `NEURATA_HOME` clonado
    de novo, mas o `config.json` ainda lembra a url)."""
    if not git_available():
        return {"ok": False, "pushed": False, "remote": remote_url,
                "error": "git indisponível — instale git"}
    if not (home.library / ".git").exists():
        return {"ok": False, "pushed": False, "remote": remote_url,
                "error": "library não versionada — rode um tick ou "
                        "`neurata snapshot`"}
    if remote_url:
        set_remote(home, remote_url)
    proc = _run(home, "remote", "get-url", "neurata")
    remote = proc.stdout.strip() if proc.returncode == 0 else None
    if not remote:
        return {"ok": False, "pushed": False, "remote": None,
                "error": "remote não configurado — use --set-remote URL"}
    pushed = _run(home, "push", "neurata", "main", timeout=PUSH_TIMEOUT)
    if pushed.returncode != 0:
        return {"ok": False, "pushed": False, "remote": remote,
                "error": pushed.stderr.strip() or "push falhou"}
    return {"ok": True, "pushed": True, "remote": remote}
