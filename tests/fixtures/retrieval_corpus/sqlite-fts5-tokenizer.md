---
id: 01TESTCORPUS000000000000AJ
type: note
env: generic
title: sqlite-fts5-tokenizer
description: "tokenizer unicode61 do fts5 e o que ele faz com pontuação"
---
O tokenizer unicode61 do fts5 corta em qualquer não-alfanumérico. Isso
quebra número com ponto em dois termos separados e derruba acento
conforme a tabela de dobra. Não existe lista de palavras vazias embutida:
preposição comum casa com o corpus inteiro.
