# Publicador QrStack no Windows

A plataforma prepara a arte (upload ou composição com a identidade do restaurante),
guarda a fila no D1 e recebe confirmações do processo Python neste computador.
O processo precisa continuar ligado e com acesso à internet. D1 não executa Python.

## Configuração local

Copiar `tools/platform.example.json` para `.local/platform.json` e preencher o
endereço HTTPS do Worker, o identificador do publicador e o mapa explícito de
restaurantes para contas Instagram. Usar o slug cadastrado na plataforma.
Não associar `testesqrstack` a um restaurante cliente: testes usam um cadastro
interno separado. O arquivo contém identificadores, nunca senhas ou tokens.

O helper `tools/platform-windows.ps1` reutiliza o vault `.local/vault.db` e sua
chave original protegida pelo Windows em `%LOCALAPPDATA%/QrStackInstagram`.
O token do serviço é gerado uma vez em `register` e salvo com DPAPI no mesmo
diretório. Ele só pode ser aberto pelo usuário Windows que o criou.

## Operação

Executar no PowerShell normal, dentro de `C:\Users\berna\qrstack-instagram`:

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File tools/platform-windows.ps1 -Action register
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File tools/platform-windows.ps1 -Action connect -Slug restaurante_interno
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File tools/platform-windows.ps1 -Action check
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File tools/platform-windows.ps1 -Action bind -Slug restaurante_interno -Disabled
```

`register` e `bind` pedem a chave administrativa QrStack de forma oculta, caso
ela não esteja no ambiente. `connect` pede a senha do Instagram apenas quando
necessário; nada aparece ao digitar, nem asteriscos. Digitar normalmente e
pressionar Enter. Não enviar senha pelo chat. Usar um terminal interativo real.

Após conferir a identidade local e remota, habilitar o vínculo na Central Stories
ou executar `bind` sem `-Disabled`. Clientes só devem ser vinculados à própria
conta e após conexão explícita. Não há login automático nem resolução de 2FA.

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File tools/platform-windows.ps1 -Action run
```

O processo consulta a fila a cada 30 segundos. Ctrl+C encerra. `-Once` processa
uma consulta. Fechar o terminal ou desligar/suspender o Windows interrompe o
serviço. O helper não instala inicialização automática nem reinício de falhas.

Para rodar sem manter um terminal aberto, acrescentar `-Background` a `-Action run`.
O PID e os logs sanitizados ficam em `.local/platform-runner.json` e
`.local/platform-runner.log`. Para encerrar esse processo, usar `-Action halt-runner`.
O controle confere PID, caminho e hora de início antes de parar o processo.
Após reiniciar o Windows ou uma falha definitiva, é preciso iniciar novamente.
Falhas temporárias da plataforma recebem até cinco esperas antes de encerrar na
sexta falha consecutiva: 30, 60, 120, 240 e 480 segundos. Um `Retry-After` maior
sempre prevalece. O modo `-Once` continua sem repetir a consulta. Apenas consulta
de fila e recuperação do journal podem repetir; uma requisição de postagem
para o Instagram nunca recebe retry automático. Falhas de autenticação/TLS e
respostas inválidas encerram a execução.

Para pausar uma conta no servidor, desmarcar **Habilitar publicação** na Central
ou usar `bind -Disabled`. Para congelar também a sessão local:

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File tools/platform-windows.ps1 -Action stop -Slug restaurante_interno
```

Uma requisição já enviada ao Instagram pode terminar mesmo após uma pausa.
O processo confere a pausa antes de cada nova etapa de rede.

## Intervalos e sinais de restrição

Esta fase mantém no máximo uma tentativa de publicação por conta em uma janela
móvel de 24 horas. O vault reserva esse intervalo atomicamente antes do acesso
ao Instagram e o estende por 24 horas após uma confirmação de publicação.
Reiniciar o processo, reconectar ou trocar o identificador do job não limpa o
limite. Na primeira atualização de um vault antigo, contas com jobs anteriores
recebem uma pausa inicial de 24 horas, pois os registros antigos não tinham data.

O Worker aplica a mesma janela por ID imutável da conta Instagram, inclusive ao
trocar o restaurante vinculado. A fila espera; novas solicitações recebem
`instagram_publication_cooldown` com `Retry-After`. Uma publicação já autorizada
continua podendo consultar sua própria permissão e confirmar o resultado. Se
o limite local for mais recente que o servidor (por exemplo, teste pelo CLI),
o job é interrompido antes do download/permissão com `local_publication_cooldown`.

Cada etapa de publicação tem um intervalo mínimo fixo de 5 segundos entre
inícios de requisições, somado às esperas já existentes no transporte. A pausa
local e a autorização remota são conferidas após essa espera. HTTP 429,
`RateLimit`, `PleaseWait` e throttling congelam a operação: a próxima conexão e
verificação remota ficam proibidas por pelo menos 24 horas ou pelo `Retry-After`
informado, prevalecendo o maior. Passar o prazo não reativa a conta: é necessária
revisão humana e reconexão explícita. Challenge, suspensão e resultado incerto
também exigem revisão e nunca acionam contorno ou reenvio.

Esses números são uma política conservadora da QrStack, não limites oficiais da
API privada nem garantia contra restrições do Instagram. O fork não é a API
oficial da Meta. Não usa rotação de IP, troca de dispositivo para contornar
bloqueios, solução automática de challenge ou atrasos aleatórios para simular
uma pessoa. Para uma operação comercial suportada, avaliar a API oficial e suas
permissões/limites publicados antes de ampliar o rollout.

## Interrupções e confirmação

- `completed`: o Instagram retornou um ID de mídia e ele foi salvo localmente.
  Conferir aparência e sticker no aplicativo; o runner não faz inspeção visual.
- `failed_attention`: operação interrompida antes da publicação; revisar a causa.
- `outcome_unknown`: não é seguro afirmar se publicou. A conta fica bloqueada.
  Não apagar jobs, trocar IDs ou criar outra tentativa para contornar o bloqueio.
- Se publicou e perdeu apenas a confirmação ao Worker, a próxima execução envia
  apenas o ACK salvo; não faz novo upload.

O journal das entregas é criptografado no vault. Backup deve preservar banco,
chave e token protegidos separadamente. Não copiar o vault sem sua chave.

## Validação

`python -m qrstack_instagram.platform` funciona sem reinstalar o entrypoint.
Os 98 testes Python são offline e bloqueiam a rede. A ponte foi testada com
respostas simuladas; isso não equivale à publicação real pela plataforma.
O teste real anterior de @testesqrstack, em 2026-10-05, pertence ao publicador
direto e está registrado em `INTERNAL_VALIDATION.md`.
