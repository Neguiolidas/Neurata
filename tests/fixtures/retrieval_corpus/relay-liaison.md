---
id: 01TESTCORPUS000000000000EN
type: note
env: generic
title: relay-liaison
description: "relay entre instâncias via liaison compartilhada"
---
O relay conecta duas instâncias por um arquivo de liaison compartilhado.
Uma escreve o pedido, a outra responde no mesmo lugar. Hub central
guarda a chave. Sem caminho compartilhado combinado dos dois lados, o
relay silencia em vez de falhar.
