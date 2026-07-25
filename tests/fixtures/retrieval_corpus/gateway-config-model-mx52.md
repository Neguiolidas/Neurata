---
id: 01TESTCORPUS0000000000005E
type: ops
env: generic
title: gateway-config-model-mx52
description: "config global de modelos e cadeia de fallback"
---
Config global de modelos reescrita. Cadeia de fallback passa por quatro
provedores em ordem fixa, com Flash no meio da fila. A config vive num
arquivo único e é lida na subida do processo. Trocar a ordem exige
reiniciar — não há reload em runtime.
