"""neurata/providers/formats/ — adapters de formato do provider genérico.

Contrato de cada módulo: `parse(path: Path, text: str) -> Scanned | None`.
Recebe o texto já lido e validado pelo walker (≤1MB, utf-8, não vazio);
devolve None só quando o conteúdo não é daquele formato. Levantar exceção
é permitido — o walker converte em `Skipped` e segue o batch.
"""

#: `fmt` → eixo de memória do espelho, escrito como `class:` no frontmatter
#: pelo harvest. O adapter é o único ponto do sistema que sabe, por contrato
#: e não por estatística, que forma leu — então é ele quem declara a classe,
#: e ela fica auditável abrindo o arquivo em vez de escondida no código.
#:
#: Prosa é documento e documento é `semantic`, mesmo quando ensina a fazer
#: algo: verbo imperativo não faz um grão procedural. Um formato que prove
#: procedimento por estrutura (frontmatter de skill, arquivo de regras) é
#: `procedural` pela forma. Formato fora do mapa não vira classe nenhuma —
#: o grão nasce sem `class:` e aparece em `missing:class`, que é o lugar
#: certo pra decidir, e não um palpite gravado no índice.
FORMAT_CLASS = {
    "skill-md": "procedural",
    "rules": "procedural",
    "yaml": "semantic",
    "markdown": "semantic",
}
