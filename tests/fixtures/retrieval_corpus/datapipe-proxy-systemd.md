---
id: 01TESTCORPUS0000000000002B
type: ops
env: generic
title: datapipe-proxy-systemd
description: "unit systemd do datapipe-proxy: restart, log e porta"
---
O serviço datapipe-proxy roda sob systemd como unit de usuário. Restart
automático com RestartSec de cinco segundos, log via journald, porta
fixada por variável de ambiente. Depois de trocar a porta é preciso
recarregar o daemon antes de reiniciar o serviço, senão a unit antiga
continua valendo.
