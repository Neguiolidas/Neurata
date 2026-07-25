---
id: 01TESTCORPUS000000000000BK
type: ops
env: generic
title: backup-restic-b2
description: "backup incremental com restic contra bucket b2"
---
Backup incremental com restic apontando pro bucket b2. Snapshot diário,
retenção de trinta dias, verificação semanal de integridade. A senha do
repositório fica fora da máquina de origem. Restore testado de verdade
uma vez por mês, senão não conta como backup.
