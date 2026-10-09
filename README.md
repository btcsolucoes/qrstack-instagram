# QrStack Instagram

Fork experimental e reduzido da instagrapi para Stories de imagem com link
interativo. Inclui a ponte para a fila QrStack (Worker + D1), executada no Windows.
Validacao real limitada a conta interna; nenhuma publicacao em clientes neste rollout.
Nenhuma garantia de estabilidade da API privada ou de ausencia de restricoes.

Origem: https://github.com/subzeroid/instagrapi

Base: versao 3.0.20, commit `a4e3be2450ed2fb1e74ca6932d80d0b19eeefb4c`.
Licenca MIT e historico original preservados. Inventario: `PRUNING.json`.
O namespace interno `instagrapi` foi preservado; nao instalar junto da biblioteca
original no mesmo ambiente virtual.

## Escopo

- Login CAA explicito e reutilizacao do estado de dispositivo/sessao.
- Consulta da propria conta e do status de seus Stories.
- Upload de imagem e uma configuracao de Story com um link HTTPS.
- Coordenadas normalizadas do link: x, y, largura, altura e rotacao.
- Vault local criptografado por conta; senha nao persistida.
- Publicador desligado por padrao, allowlist explicita e pausa por conta.
- Confere username e ID imutavel da sessao com a conta vinculada antes de publicar.
- Jobs persistidos em SQLite com reserva atomica e bloqueio de duplicacao.
- Limite persistente de uma tentativa por conta a cada 24 horas, buffer fixo
  entre etapas e pausa com revisao humana apos restricoes do Instagram.
- Consumo autenticado da fila D1, mapa restaurante/conta local e recuperacao apenas de confirmacoes.

Operacao da integracao: [PLATFORM_WINDOWS.md](PLATFORM_WINDOWS.md).

A gestao tambem pode solicitar uma conexao explicita pelo painel de Stories.
A senha tem entrega unica e nao e salva no Windows; o runner confere usuario e
ID imutavel antes de aprovar o vinculo. O status exibido distingue a ultima
confirmacao da sessao da disponibilidade do publicador. Nao ha reconexao
automatica nem novas tentativas de login apos queda ou challenge.

Removidos: follows, likes, comentarios, Direct, scraping, busca de usuarios,
cadastro de contas, mudanca de senha, feed, albums, videos, Reels, IGTV,
notificacoes, insights, destaques, desafios automaticos e retries de publicacao.
Modelos auxiliares foram reduzidos a StoryLink e StoryResizeMode.

Transporte, criptografia de login e parsing CAA continuam necessarios. Reduzir
o codigo nao torna uma API privada oficialmente suportada.

## Estado dos testes

Testes locais sem acesso de rede validam isolamento, criptografia, bloqueios,
persistencia de jobs, ausencia de retries e payload do sticker. Em 2026-10-05,
o primeiro teste em @testesqrstack confirmou login real, reutilizacao da sessao
em outro processo, publicacao e presenca do Story em consulta remota.
Renderizacao, clicabilidade e duracao da sessao ainda precisam de validacao.
O sticker usa `link_sticker_default`; translucidez NAO foi validada nem prometida.
Nenhuma conta do restaurante deve ser usada nesta fase.

Revisao e roteiro da validacao interna: [INTERNAL_VALIDATION.md](INTERNAL_VALIDATION.md).
Os testes bloqueiam requests, sockets e o transporte nativo curl_cffi.

## Preparacao no Windows

```powershell
cd C:\Users\berna\qrstack-instagram
python -m venv .venv
.venv\Scripts\python -m pip install -e '.[test]'
.venv\Scripts\qrstack-instagram --help
.venv\Scripts\python -m pytest -q
```

O ambiente local ja esta instalado. PyCryptodomex esta fixado em 3.23.0:
o binario 3.24.0 nao carregou neste Windows. CI tambem testa Python 3.12.
Depois de adicionar pacotes ao fork, reinstalar o projeto editavel. Um registro
antigo pode deixar o pytest passar pelo diretorio atual e quebrar o comando CLI.

Antes de conectar, fornecer `QRSTACK_VAULT_KEY` pelo ambiente de execucao ou
gerenciador de segredos. Deve ser uma chave Fernet de 32 bytes em base64 URL-safe.
Gerar uma unica vez com `Fernet.generate_key()` e guardar fora do repositorio.
Nao imprimir em logs, enviar ao chat ou perder a chave: sem ela o vault nao abre.
O arquivo `.local/vault.db` precisa de backup junto de uma copia segura separada
da chave. Sessoes sao credenciais sensiveis, mesmo criptografadas.

```powershell
$env:QRSTACK_ENABLE_PRIVATE_PUBLISHER = '1'
$env:QRSTACK_TEST_ACCOUNTS = 'usuario_interno_de_teste'
.venv\Scripts\qrstack-instagram connect usuario_interno_de_teste
```

A senha e solicitada de forma oculta pelo terminal, apenas para autenticacao.
Nao colocar senha em argumentos. Nunca fazer login automatico diario.
Se houver challenge/2FA/checkpoint, esta versao para e exige verificacao humana.
Nao ha resolvedor automatico nem onboarding completo de 2FA nesta fase.
Resolver no aplicativo oficial; so depois avaliar uma reconexao explicita.

`connect` reutiliza a sessao salva quando ela continua valida. Se a sessao expirou,
nao existe fallback automatico: depois da revisao humana, usar explicitamente
`connect usuario_interno_de_teste --fresh-login`. Essa opcao limpa credenciais da
sessao em memoria e preserva a identidade do dispositivo. Nao contorna 2FA ou
challenge. Jobs PENDING/UNKNOWN bloqueiam tambem a reconexao.

## Teste de publicacao (somente apos autorizacao)

```powershell
.venv\Scripts\qrstack-instagram status usuario_interno_de_teste
.venv\Scripts\qrstack-instagram verify usuario_interno_de_teste
.venv\Scripts\qrstack-instagram publish usuario_interno_de_teste --image C:\caminho\story.jpg --url https://example.com/teste-interno --job teste-dia-01
.venv\Scripts\qrstack-instagram status usuario_interno_de_teste --job teste-dia-01
.venv\Scripts\qrstack-instagram verify usuario_interno_de_teste --job teste-dia-01
```

Substituir conta, imagem, URL e job pelos dados do teste interno combinado.
`status` consulta apenas o vault e identifica a origem como LOCAL. `verify` faz
uma consulta remota explicita da propria conta, sem login ou publicacao. Com
`--job`, consulta a presenca do Story confirmado: PRESENT ou NOT_FOUND. NOT_FOUND
nao prova falha de publicacao (o Story pode ter expirado) e nunca autoriza reenvio.
Jobs sem ID de Story confirmado exigem inspecao manual e continuam bloqueados.
`verify` nao descongela a conta nem reconcilia jobs.

O harness exige arte 1080x1920, em JPG/JPEG/PNG/WEBP, validada antes de reservar
o job ou acessar a rede. Inspecionar o Story no aplicativo, tocar no sticker e
confirmar o destino. O comando nao verifica visualmente a publicacao.
O link usa centro x=0.5, y=0.72, largura=0.56, altura=0.10 no harness;
o cliente permite ajustar isso por StoryLink. Testar no maximo um Story/dia.

Um job concluido nao pode ser reenviado. Erro durante o processo fica UNKNOWN
e bloqueia novos jobs daquela conta, inclusive apos reiniciar. Conferir no
Instagram se houve publicacao antes de qualquer reconciliacao; nao apagar o job
ou criar outro ID para tentar contornar o bloqueio. A ferramenta ainda nao possui
reconciliacao automatica nem painel de administracao.

## Execucao interna assistida no Windows

Para o teste combinado de 2026-10-04, foi preparada a arte local
`.local/internal-story.png` para `@testesqrstack`, com destino
`https://btcsolucoes.github.io/carda-pio/`. O gerador local e
`tools/make_internal_story.py`.

O script abaixo pede a senha no terminal, executa connect, verify, uma tentativa
de publish e verify do Story, e termina congelando a conta e desligando seu
proprio processo. Falha em qualquer etapa interrompe a sequencia sem retry.

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File C:\Users\berna\qrstack-instagram\tools\run_internal_test.ps1 -Account testesqrstack -Image C:\Users\berna\qrstack-instagram\.local\internal-story.png -TargetUrl https://btcsolucoes.github.io/carda-pio/ -Job internal-testesqrstack-2026-10-04-01
```

`-ExecutionPolicy RemoteSigned` vale apenas para esse processo PowerShell e seus
filhos; nao altera a politica permanente do usuario ou da maquina. Permite este
script criado localmente sem exigir assinatura. Politicas de grupo, se existentes,
continuam tendo precedencia. Nao e necessario abrir o terminal como administrador.

Se QRSTACK_VAULT_KEY nao estiver no ambiente e ainda nao existir vault, o script
gera uma chave e guarda somente sua forma protegida pelo DPAPI do usuario
Windows em `%LOCALAPPDATA%\QrStackInstagram\vault-key.dpapi`, fora do repositorio.
Essa protecao depende do perfil Windows: nao substitui uma copia recuperavel da
chave em um gerenciador de segredos. Um vault existente sem chave correspondente
interrompe a preparacao; nunca e sobrescrito com outra chave.

O resultado operacional sem segredos fica em `.local/<job>.result.json`. A
existencia desse arquivo impede relancar a mesma tentativa pelo script, inclusive
se houve falha de login. Nao apagar o registro nem trocar o job para repetir um
erro sem revisao. O script nao implementa reconciliacao nem resolve 2FA.

## Parada

```powershell
.venv\Scripts\qrstack-instagram stop usuario_interno_de_teste
$env:QRSTACK_ENABLE_PRIVATE_PUBLISHER = '0'
```

O primeiro comando persiste FROZEN. O segundo desliga o processo iniciado com
esse ambiente; nao altera variaveis de outros processos ja em execucao. Nenhum
kill switch pode desfazer uma requisicao ja enviada ao Instagram. O estado e
rechecado entre as etapas de publicacao e salvo com controle de revisao para que
uma operacao em andamento nao sobrescreva o stop. A parada e cooperativa: nao
cancela uma etapa que ja passou pela checagem. Se a API ja confirmou o Story,
o job permanece PUBLISHED mesmo se houver stop ou falha ao salvar a sessao depois.

## Antes de producao

Faltam testes reais por varias semanas, onboarding/reconnect de 2FA controlado,
reconciliacao de resultado incerto, gestor de segredos, auditoria
persistente, controles operacionais distribuidos e adapter comercial verificado.
Nao existe fallback automatico para outro provider apos erro de conta.
Storrito nao foi integrado; sua API/contrato precisarao ser verificados.
Os comandos diretos nao alteram cardapios, Forms ou Sheets. A ponte registra
somente etapas e resultados dos jobs de Stories na plataforma.
