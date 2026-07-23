"""tests/test_property_no_destructive.py — T7: property test zero operação
destrutiva.

Gerador determinístico (seed fixo, stdlib `random`, sem dependência nova)
de sequências de operações públicas (deposit, query, expand, tick,
harvest, snapshot, reindex) sobre uma library temporária. Invariante:
o conjunto de "corpos" depositados (identificados por content_hash) nunca
perde membro nem sofre truncamento de conteúdo em nenhum ponto da
sequência — mover pra quarantine preserva os bytes do corpo.

`harvest` usa um fixture local (`skills_dir` sob `tmp_path`, fora do
home) — nada de rede/FS real fora do diretório temporário do teste.
"""
import hashlib
import random
from pathlib import Path

from neurata.deposit import deposit
from neurata.expand import expand
from neurata.frontmatter import parse
from neurata.harvest import harvest
from neurata.home import NeurataHome
from neurata.query import query
from neurata.reindex import reindex
from neurata.snapshot import commit_manual, git_available
from neurata.tick import curate_tick

SEED = 20260722
N_STEPS = 80
_ALL_MD_DIRS = ("inbox", "library", "archive", "quarantine")

_WORDS = ["python", "git", "index", "skill", "nota", "corpo", "teste",
          "sistema", "arquivo", "dedup"]


def _mkhome(tmp_path):
    home = NeurataHome(tmp_path / "neurata")
    home.init()
    return home


def _write_skill(skills_dir, dirname, name, body):
    d = skills_dir / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: desc {name}\n---\n{body}\n",
        encoding="utf-8")


def _all_bodies_by_hash(home) -> dict:
    """Mapa content_hash(corpo) -> corpo, achatado por todo o home tree
    (inbox/library/archive/quarantine). Usa `content_hash` do frontmatter
    quando presente (estável através de rename/re-serialize no tick);
    cai pra sha256 do body cru se o arquivo não tiver meta parseável."""
    out = {}
    for dirname in _ALL_MD_DIRS:
        d = getattr(home, dirname)
        if not d.exists():
            continue
        for f in d.glob("*.md"):
            try:
                meta, body = parse(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            chash = meta.get("content_hash") or hashlib.sha256(
                body.encode("utf-8")).hexdigest()
            out[str(chash)] = body
    return out


def test_zero_operacao_destrutiva(tmp_path):
    rng = random.Random(SEED)
    home = _mkhome(tmp_path)
    skills_dir = tmp_path / "skills"
    known_ids = []  # ids retornados por deposit (action == created)
    skill_n = 0

    # baseline: nenhum corpo conhecido ainda.
    seen_bodies = {}

    def _assert_no_loss(step_no, op_name):
        current = _all_bodies_by_hash(home)
        missing = []
        truncated = []
        for chash, body in seen_bodies.items():
            if chash not in current:
                missing.append(chash)
                continue
            if current[chash] != body:
                truncated.append(chash)
        assert not missing, (
            f"step {step_no} ({op_name}, seed={SEED}): corpo(s) perdido(s) "
            f"após op — hashes {missing}")
        assert not truncated, (
            f"step {step_no} ({op_name}, seed={SEED}): corpo(s) truncado(s)/"
            f"alterado(s) após op — hashes {truncated}")

    def op_deposit():
        content = f"Titulo {rng.randint(0, 10**9)}\n\n" + " ".join(
            rng.choice(_WORDS) for _ in range(rng.randint(3, 12)))
        result = deposit(home, content=content)
        if result["action"] == "created":
            known_ids.append(result["id"])
        chash = result["hash"]
        # registra o corpo tal como ficou gravado (fonte de verdade: arquivo).
        for dirname in _ALL_MD_DIRS:
            d = getattr(home, dirname)
            for f in d.glob("*.md"):
                try:
                    meta, body = parse(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if meta.get("content_hash") == chash:
                    seen_bodies[chash] = body
                    return

    def op_query():
        try:
            query(home, rng.choice(_WORDS), limit=5)
        except Exception:
            pass  # query com índice vazio/ausente não é destrutivo

    def op_expand():
        if not known_ids:
            return
        eid = rng.choice(known_ids)
        grain = rng.choice(("card", "summary", "full"))
        try:
            expand(home, eid, grain=grain)
        except Exception:
            pass  # ref pode já ter migrado (rename/tombstone) — não destrutivo

    def op_tick():
        curate_tick(home, budget=rng.choice((5, 10, 20)))

    def op_harvest():
        nonlocal skill_n
        skill_n += 1
        _write_skill(skills_dir, f"s{skill_n}", f"skill-{skill_n}",
                     f"Corpo do skill {skill_n}: " + " ".join(
                         rng.choice(_WORDS) for _ in range(5)))
        harvest(home, "claude-code", skills_dir=skills_dir)

    def op_snapshot():
        if not git_available():
            return
        commit_manual(home)

    def op_reindex():
        reindex(home)

    ops = [op_deposit, op_deposit, op_deposit, op_query, op_expand,
          op_tick, op_harvest, op_snapshot, op_reindex]

    for step in range(N_STEPS):
        op = rng.choice(ops)
        op()
        _assert_no_loss(step, op.__name__)

    # sweep final: mais um tick pra drenar tudo que ficou pendente e
    # reconferir a invariante no estado terminal.
    curate_tick(home, budget=100)
    _assert_no_loss(N_STEPS, "final_tick")
