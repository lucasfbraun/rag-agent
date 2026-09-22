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

### 3. Persistencia no historico

Adicionar `source_refs JSONB nullable` em `conversation_messages`.

Isso permite que um pedido posterior use as fontes ja resolvidas da ultima
resposta, sem depender de nova busca vetorial nem de decisao do LLM.

### 4. Interface no frontend

Onde o chat hoje mostra apenas os nomes em `sources`, renderizar as fontes com
acao de download quando `source_refs` existir. Mensagens antigas sem
`source_refs` continuam mostrando o texto atual.

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

## Sequencia de commits

Cada etapa finalizada deve ser documentada e commitada separadamente:

1. documentar o plano;
2. criar o modulo de fontes baixaveis e seus testes;
3. expor o endpoint de download e seus testes;
4. persistir `source_refs` no historico;
5. retornar `source_refs` no agente e no streaming;
6. renderizar downloads no frontend;
7. adicionar o atalho conversacional para "traga o arquivo".

