---
id: 01TESTCORPUS0000000000006F
type: ops
env: generic
title: ruff-config-pyproject
description: "ruff no pyproject, ordenação de import e pyright"
---
Config do ruff movida pro pyproject. Regra de ordenação de import ligada,
o que quebrou um re-export que o autofix removeu como unused. Pyright
reclamou de dois retornos sem anotação. Ambos resolvidos sem afrouxar
regra nenhuma.
