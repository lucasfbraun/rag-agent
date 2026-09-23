# Download de fontes do RAG

## Objetivo

Permitir que o agente entregue, junto da resposta, referencias baixaveis dos
arquivos consultados no RAG. O usuario tambem deve conseguir pedir depois algo
como "agora me traga o arquivo" e receber os mesmos arquivos usados na resposta
anterior da conversa.

## Principio de desenho

O Qdrant continua sendo o indice de busca. Ele guarda metadados (`filename` e
`filepath`) para ligar cada trecho ao arquivo original, mas nao deve servir o
arquivo. O download deve passar por um modulo proprio, com uma interface pequena:

- montar referencias baixaveis a partir dos documentos recuperados;
- deduplicar referencias por arquivo;
- gerar identificadores opacos, sem expor caminho absoluto;
- resolver o identificador para um arquivo permitido;
- bloquear arquivos fora das raizes configuradas;
- entregar o arquivo por endpoint autenticado.

Esse modulo concentra a complexidade de seguranca e preserva o agente como
orquestrador de resposta, nao como servidor de arquivos.

## Etapas

### 1. Fontes estruturadas

Adicionar uma representacao estruturada de fonte:

```json
{
  "id": "token_opaco",
  "nome_arquivo": "Boletim FLEXX AG 2032.pdf",
  "download_url": "/api/documentos/fontes/token_opaco/download"
}
```

O campo `sources` atual continua existindo como lista de nomes para
compatibilidade. O novo campo `source_refs` carrega os dados de download.

Status: concluido no modulo `app.document_source_service`. A interface publica
da etapa e `montar_source_refs(docs)` e `resolver_source_id(source_id)`. O id da
fonte e derivado de HMAC do caminho canonico e o caminho absoluto nao aparece na
resposta JSON.

### 2. Endpoint de download

Criar:

```http
GET /api/documentos/fontes/{source_id}/download
```

Regras:

- exigir usuario autenticado com permissao `VIEW_CATALOG`;
- retornar `FileResponse`;
- usar `Content-Disposition` com nome original;
- nunca retornar `filepath` ao cliente;
- responder `404` para referencia inexistente ou arquivo removido;
- responder `400` para identificador invalido.

Status: concluido em `GET /api/documentos/fontes/{source_id}/download`. O
endpoint exige `VIEW_CATALOG`, resolve o id pelo modulo de fontes e traduz id
malformado para `400` e arquivo ausente para `404`.

### 3. Persistencia no historico

Adicionar `source_refs JSONB nullable` em `conversation_messages`.

Isso permite que um pedido posterior use as fontes ja resolvidas da ultima
resposta, sem depender de nova busca vetorial nem de decisao do LLM.

Status: concluido na migration `9b1a2c3d4e5f`, no model
`ConversationMessage`, no `save_exchange` e nos endpoints de conversa. O campo
e opcional e mensagens antigas voltam com `source_refs: []`.

### 4. Interface no frontend

Onde o chat hoje mostra apenas os nomes em `sources`, renderizar as fontes com
acao de download quando `source_refs` existir. Mensagens antigas sem
`source_refs` continuam mostrando o texto atual.

Pre-requisito concluido: o agente sincrono e o streaming ja publicam
`source_refs`. No caminho conversacional, as referencias sao montadas a partir
dos payloads recuperados do Qdrant; nos demais caminhos, o campo sai como lista
vazia ate existir caminho fisico confiavel para aquelas fontes deterministicas.

Status: concluido. O chat preserva `source_refs` ao carregar historico e ao
receber respostas novas; quando existem referencias estruturadas, exibe acao de
download autenticado. Mensagens antigas continuam usando apenas `sources`.

### 5. Pedido conversacional de arquivo

Detectar pedidos como:

- "me traga o arquivo";
- "baixar arquivo";
- "manda o boletim";
- "traga o PDF";
- "me envie a fonte";
- "download".

Quando o pedido tiver `conversation_id`, buscar a ultima resposta do assistente
com `source_refs` e devolver uma resposta curta com essas mesmas referencias.
Se o usuario citar um termo especifico, filtrar pelo nome do arquivo.

Status: concluido. `/api/match` e `/api/match/stream` detectam pedidos de
arquivo usando o historico da conversa, reutilizam a ultima resposta do
assistente com `source_refs`, filtram por termo quando o usuario cita codigo ou
nome, persistem o novo turno e nao chamam RAG/LLM nesse caminho.

## Validacao por subagentes

Status: concluido. Foram instanciados dois subagentes de revisao:

- Standards: nao encontrou violacao dura de padrao documentado, mas apontou
  oportunidades de refinamento: tipar melhor `source_refs`, mover o endpoint de
  download para um router mais coeso e reduzir duplicacao na montagem de eventos
  `meta` do streaming.
- Spec: encontrou tres ajustes necessarios antes de considerar a entrega
  completamente fechada: preservar melhor o nome original no download quando o
  arquivo fisico tem prefixo tecnico, restringir o detector de pedido de arquivo
  para nao capturar perguntas normais sobre boletim/PDF/documento, e alinhar o
  streaming para aplicar o atalho apenas quando houver `conversation_id`.

Testes executados nesta etapa:

```powershell
python -m pytest tests/test_document_source_service.py tests/test_document_source_download_endpoint.py tests/test_agent_source_refs.py tests/test_agent_scope.py tests/test_tool_calling_sequence.py tests/test_conversation_file_shortcut.py --basetemp=../.tmp_pytest_review
python -m pytest frontend/tests/test_conversation_history_ui.py frontend/tests/test_documentos_ui.py --basetemp=.tmp_pytest_frontend_review
```

Resultado: 30 testes backend e 19 testes frontend passaram. Permanece uma
pendencia de validacao com banco real para testes dependentes de PostgreSQL,
porque o ambiente local nao autenticou o usuario configurado anteriormente.

## Correcao de deploy

Status: concluido. A primeira subida em Ubuntu apos `git pull` falhou no startup
do backend porque o Alembic encontrou dois heads (`9b1a2c3d4e5f` e
`a3d6f81c47e9`) ao rodar `alembic upgrade head`. Foi adicionada uma merge
revision sem mudanca de schema para unir as duas linhas de migration e um teste
de regressao para garantir que o grafo volte a ter um unico head.

## Correcao do pedido "me traga o arquivo"

Status: concluido. Em consulta real sobre AG 2032, a resposta exibiu `sources`
com os PDFs consultados, mas o pedido seguinte "me traga o arquivo" respondeu
que nao havia arquivo recuperado. A causa era que `source_refs` so era montado
quando o `filepath` do Qdrant ja era baixavel no ambiente atual; se o caminho
vinha de outro ambiente ou ficava fora das raizes do container, o agente
mostrava o nome da fonte mas persistia `source_refs: []`.

Agora o modulo de fontes tenta primeiro o `filepath` permitido e, se ele nao
servir, resolve o arquivo pelo `filename` dentro das raizes configuradas. O
orquestrador tambem enriquece respostas que tenham `sources` sem `source_refs`,
incluindo metadados de streaming. Assim, apos uma resposta que cite
`Boletim FLEXX AG 2032 ESP.pdf`, o turno seguinte pode reutilizar a fonte e
mostrar o download.

## Atualizacao de configuracao

Status: concluido. O `.env.example` documenta `RAG_DOWNLOAD_ROOTS` com as raizes
padrao do Docker e o caminho `/mnt/acervo`, usado quando o acervo de origem esta
montado a partir de um compartilhamento SMB.

## Correcao de performance do download

Status: concluido. A resolucao por `filename` funcionava para arquivos em SMB,
mas podia varrer recursivamente o acervo montado a cada resposta do agente.
Foi adicionado `RAG_DOWNLOAD_PATH_ALIASES` para mapear o prefixo gravado no
Qdrant diretamente para o mount local do container, por exemplo
`//10.1.1.205/flexivel/GRUPOS/Qualidade/Documentação de Produto=/mnt/acervo`.
Com esse alias, `source_refs` sao montadas por caminho direto e o fallback por
nome fica apenas para casos sem mapeamento. O resolver de download tambem guarda
em cache local os ids emitidos durante a resposta, evitando varredura no clique
imediato do usuario.

## Download direto em conversa nova

Status: concluido. O pedido direto de arquivo em uma conversa sem historico,
por exemplo "me traga o arquivo do AG 2032", agora segue para uma recuperacao
deterministica no acervo em vez de responder que ainda nao ha arquivo na
conversa. Quando o pedido contem intencao de download e um termo especifico, o
motor busca os documentos no RAG, monta `source_refs` e retorna os botoes de
download sem chamar o LLM. Pedidos genericos como "me traga o arquivo", sem
produto/documento citado e sem fontes anteriores, continuam recebendo a mensagem
orientando a consultar o acervo primeiro.

## Precisao por codigo explicito

Status: concluido. Quando o pedido direto de download cita um codigo de produto,
como "CL 2060", os documentos recuperados pelo RAG agora sao filtrados por match
exato desse codigo no nome/caminho do arquivo antes de montar os downloads. Isso
evita retornar vizinhos semanticos ou documentos relacionados, como `CL 2081`,
ISO ou propostas comerciais, quando o usuario pediu um produto especifico.

Status adicional: concluido. Se o pedido cita um codigo explicito e nenhum
documento recuperado bate exatamente com ele, o fluxo nao cai mais para os
vizinhos semanticos. Em vez disso, responde que nao encontrou arquivo com match
exato para o codigo pedido.

## Sequencia de commits

Cada etapa finalizada deve ser documentada e commitada separadamente:

1. documentar o plano;
2. criar o modulo de fontes baixaveis e seus testes;
3. expor o endpoint de download e seus testes;
4. persistir `source_refs` no historico;
5. retornar `source_refs` no agente e no streaming;
6. renderizar downloads no frontend;
7. adicionar o atalho conversacional para "traga o arquivo".
8. revisar e testar a entrega com subagentes.
9. corrigir o grafo de migrations para o deploy.
10. resolver fontes por nome quando o caminho do indice nao e baixavel.
11. documentar `RAG_DOWNLOAD_ROOTS` no `.env.example`.
12. adicionar alias de caminho para evitar varredura lenta do SMB.
13. permitir download direto de arquivo em conversa nova quando o pedido cita produto/documento.
14. filtrar download direto por codigo exato quando o usuario cita um produto.
15. bloquear fallback para vizinhos quando nao ha match exato do codigo.
