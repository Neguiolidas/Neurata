"""neurata.mcp — servidor MCP da Neurata, em biblioteca padrão pura.

O `main` NÃO é reexportado aqui de propósito. Importar `neurata.mcp.server` no
momento do `import neurata.mcp` traria a cadeia inteira de `query`, `deposit`,
`expand` e `shelf` — 284 ms medidos — para dentro de qualquer coisa que só
quisesse olhar o pacote. O ponto de entrada `neurata-mcp` aponta direto para
`neurata.mcp.server:main`.
"""
