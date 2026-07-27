"""neurata/providers/formats/ — adapters de formato do provider genérico.

Contrato de cada módulo: `parse(path: Path, text: str) -> Scanned | None`.
Recebe o texto já lido e validado pelo walker (≤1MB, utf-8, não vazio);
devolve None só quando o conteúdo não é daquele formato. Levantar exceção
é permitido — o walker converte em `Skipped` e segue o batch.
"""
