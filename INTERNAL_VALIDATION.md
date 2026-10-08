# Validacao interna — revisao em 2026-10-04, teste real em 2026-10-05

Branch: `codex/minimal-story-publisher`. Revisao do README e da implementacao
concluida antes da execucao dos testes desta etapa. Uso restrito a uma conta
interna. Sem integracao com a plataforma e sem publicacao em contas de clientes.

## Problemas encontrados e corrigidos

| Problema | Correcao e evidencia offline |
| --- | --- |
| A sessao podia pertencer a outra conta apesar da allowlist do argumento. | Comparacao do username, ID retornado, ID da sessao e ID salvo; respostas divergentes/incompletas bloqueiam connect, publish e verify. |
| Um stop podia ser perdido ao salvar um estado antigo. | Revisao no payload criptografado e gravacao condicional em transacao; testes com duas conexoes SQLite e parada entre upload e configuracao. |
| O bloqueio de sockets do pytest nao cobria o libcurl nativo. | Bloqueios em requests.Session, curl_cffi.Session e Curl.perform, alem de sockets. |
| PNG/WebP convertidos para JPEG mantinham tipo de conteudo incorreto. | O upload anuncia JPEG; os testes decodificam os bytes reais preparados. |
| Requisicoes de login, registro do dispositivo e upload nao tinham timeout explicito. | Timeout de 25 segundos por requisicao, sem redirects automaticos nem repeticao de POST. |
| O status local podia ser confundido com validacao remota. | status identifica LOCAL; verify consulta a conta e opcionalmente o Story confirmado, sem reautenticar ou publicar. |
| Reconnect repetia a validacao de uma sessao expirada sem um caminho explicito para novo login. | connect --fresh-login limpa a sessao em memoria e mantem o dispositivo. Sem fallback automatico ou resolvedor de desafios. |
| A instalacao editavel local nao incluia o pacote qrstack_instagram. | Projeto reinstalado; comando CLI incluido na verificacao de CI. |

## Validacao local

- Testes: 41 aprovados, incluindo os oito anteriores.
- `pip check`: dependencias consistentes.
- Comando instalado `qrstack-instagram --help`: aprovado fora da raiz do projeto.
- Sintaxe de `tools/run_internal_test.ps1`: validada pelo parser PowerShell.
- CI Linux/Python 3.12 configurado, mas nao executado nesta sessao.
- Os testes locais nao acessam o Instagram. O primeiro teste real, separado
  dessa suite, esta registrado abaixo e nao substitui a verificacao visual.

## Sequencia do primeiro teste real

1. Informar o @ da conta interna, o caminho da arte 1080x1920 e a URL HTTPS.
   Configurar QRSTACK_VAULT_KEY fora do chat/repositorio, habilitar o publicador
   e incluir somente a conta interna na allowlist. Guardar a chave com seguranca.
2. Executar connect no terminal; o operador digita a senha no prompt oculto.
   Se houver challenge/2FA/checkpoint, parar e revisar no aplicativo oficial.
3. Executar verify em novo processo para testar o uso da sessao salva.
4. Publicar uma unica vez com um job novo. No maximo um Story por dia;
   esse limite continua sendo operacional, sem contador diario automatico.
5. Consultar status --job e verify --job. No aplicativo oficial, conferir
   a conta, a arte, a posicao do sticker, o toque e o destino final do link.
6. Se houver erro ou interrupcao, consultar o job e inspecionar o aplicativo.
   UNKNOWN/PENDING nao devem ser apagados nem contornados com outro ID.
   Nao ha comando de reconciliacao nesta versao.
7. Executar stop e desligar o publicador no ambiente usado. Em outra data,
   uma consulta verify explicita pode avaliar a duracao da sessao, sem login diario.

## Primeiro teste real — 2026-10-05

Execucao concluida as 15:09:18 no horario de Sao Paulo (18:09:18 UTC).
Fonte: `.local/internal-testesqrstack-2026-10-04-01.result.json`.
O job manteve o ID preparado no dia anterior; a execucao ocorreu em 2026-10-05.
Todas as quatro etapas e o stop terminaram com exit code 0.

| Verificacao | Resultado |
| --- | --- |
| Conta interna e materiais definidos | @testesqrstack; .local/internal-story.png; https://btcsolucoes.github.io/carda-pio/ |
| Login CAA real | Aprovado; connect encerrou com sucesso e verificou a identidade |
| Sessao reaproveitada em novo processo | Aprovado; verify retornou session=VALID para testesqrstack |
| Story publicado uma unica vez | Uma tentativa concluida como PUBLISHED; ID 4001397025916030206 |
| Presenca consultada no Instagram | Confirmada; verify --job retornou story=PRESENT |
| Parada apos o teste | stop concluiu com exit code 0; publicador desligado no processo do runner |
| Renderizacao, coordenadas e aparencia do sticker | Nao observado |
| Toque e destino final do link | Nao observado |
| Duracao da sessao em dias posteriores | Nao observado |

Nao registrar senhas, chaves, cookies, respostas brutas de login ou dumps de
sessao neste arquivo. Registrar apenas resultados operacionais sem segredos.

Historico da preparacao: `internal-testesqrstack-2026-10-04-01`. A abertura automatica
do PowerShell interativo foi recusada pela revisao automatica de aprovacao com
o motivo `blocked by policy`, antes de criar chave, vault ou registro da tentativa.
O operador iniciou o comando documentado no README em seu terminal e digitou a
senha no prompt oculto. A senha foi aceita; a ausencia de caracteres no terminal
era o comportamento esperado do prompt.

Em 2026-10-05, apos o bloqueio de scripts mostrado pelo operador, os escopos de
ExecutionPolicy foram consultados: todos Undefined, sem politica de grupo
configurada. Naquele momento, o arquivo de resultado ainda nao existia. O README
foi corrigido para iniciar PowerShell com RemoteSigned apenas nessa sessao, sem
mudar CurrentUser ou LocalMachine. O ID da tentativa preparada foi mantido.

Resultado atual: COMPLETED_NEEDS_VISUAL_CHECK. Nao publicar outro Story para
repetir este teste. A confirmacao visual e do toque deve usar o Story ja publicado.
