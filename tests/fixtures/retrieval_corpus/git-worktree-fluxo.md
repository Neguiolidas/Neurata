---
id: 01TESTCORPUS0000000000004D
type: note
env: generic
title: git-worktree-fluxo
description: "fluxo de branch, merge e push usando worktree isolada"
---
Cada feature nasce numa worktree isolada. Branch criada a partir do
remoto, commits pequenos, merge de volta só depois dos testes passarem.
O push acontece uma vez por branch. Worktree removida depois do merge,
nunca antes — remover antes deixa a branch travada.
