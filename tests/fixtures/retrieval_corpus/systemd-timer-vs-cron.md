---
id: 01TESTCORPUS000000000000CL
type: note
env: generic
title: systemd-timer-vs-cron
description: "timer do systemd contra cron: quando cada um vale"
---
Timer do systemd dá log estruturado, dependência entre units e execução
perdida recuperável. Cron dá uma linha e nada mais. Pra tarefa que
precisa saber se rodou e por que falhou, timer ganha. Pra tarefa
descartável de uma linha, cron continua mais barato.
