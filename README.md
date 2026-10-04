# QrStack Instagram

Fork experimental e reduzido da instagrapi, exclusivamente para testar Stories
de imagem com link interativo em uma conta interna. Nao integrado ao QrStack.
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
- Publicador desligado por padrao, allowlist de contas internas e pausa por conta.
- Jobs persistidos em SQLite com reserva atomica e bloqueio de duplicacao.

Removidos: follows, likes, comentarios, Direct, scraping, busca de usuarios,
cadastro de contas, mudanca de senha, feed, albums, videos, Reels, IGTV,
notificacoes, insights, destaques, desafios automaticos e retries de publicacao.
Modelos auxiliares foram reduzidos a StoryLink e StoryResizeMode.

Transporte, criptografia de login e parsing CAA continuam necessarios. Reduzir
o codigo nao torna uma API privada oficialmente suportada.

## Estado dos testes

Testes locais sem acesso de rede validam isolamento, criptografia, bloqueios,
persistencia de jobs, ausencia de retries e payload do sticker. Login real,
renderizacao, clicabilidade e duracao da sessao ainda precisam de teste interno.
O sticker usa `link_sticker_default`; translucidez NAO foi validada nem prometida.
Nenhuma conta do restaurante deve ser usada nesta fase.

## Preparacao no Windows

```powershell
cd C:\Users\berna\qrstack-instagram
python -m venv .venv
.venv\Scripts\python -m pip install -e '.[test]'
.venv\Scripts\python -m pytest -q
```

O ambiente local ja esta instalado. PyCryptodomex esta fixado em 3.23.0:
o binario 3.24.0 nao carregou neste Windows. CI tambem testa Python 3.12.

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

## Teste de publicacao (somente apos autorizacao)

```powershell
.venv\Scripts\qrstack-instagram status usuario_interno_de_teste
.venv\Scripts\qrstack-instagram publish usuario_interno_de_teste --image C:\caminho\story.jpg --url https://tinyurl.com/amaromenu --job teste-dia-01
```

Usar arte 1080x1920. Inspecionar o Story no aplicativo, tocar no sticker e
confirmar o destino. O comando nao verifica visualmente a publicacao.
O link usa centro x=0.5, y=0.72, largura=0.56, altura=0.10 no harness;
o cliente permite ajustar isso por StoryLink. Testar no maximo um Story/dia.

Um job concluido nao pode ser reenviado. Erro durante o processo fica UNKNOWN
e bloqueia novos jobs daquela conta, inclusive apos reiniciar. Conferir no
Instagram se houve publicacao antes de qualquer reconciliacao; nao apagar o job
ou criar outro ID para tentar contornar o bloqueio. A ferramenta ainda nao possui
reconciliacao automatica nem painel de administracao.

## Parada

```powershell
.venv\Scripts\qrstack-instagram stop usuario_interno_de_teste
$env:QRSTACK_ENABLE_PRIVATE_PUBLISHER = '0'
```

O primeiro comando persiste FROZEN. O segundo desliga o processo iniciado com
esse ambiente; nao altera variaveis de outros processos ja em execucao. Nenhum
kill switch pode desfazer uma requisicao ja enviada ao Instagram.

## Antes de producao

Faltam testes reais por varias semanas, onboarding/reconnect de 2FA controlado,
reconciliacao de resultado incerto, limites diarios, gestor de segredos, auditoria
persistente, controles operacionais distribuidos e adapter comercial verificado.
Nao existe fallback automatico para outro provider apos erro de conta.
Storrito nao foi integrado; sua API/contrato precisarao ser verificados.
Este fork nao altera cardapios, Forms, Sheets, D1 ou os sites publicados.
