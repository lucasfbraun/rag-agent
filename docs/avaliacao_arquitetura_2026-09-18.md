# Avaliação de arquitetura e correção do caminho leigo

Data: 18/09/2026.

Continuação de [avaliacao_agente_2026-09-10.md](avaliacao_agente_2026-09-10.md).
Aquele documento avaliou a qualidade do agente; este avalia a **arquitetura**, a
partir de um sintoma novo relatado pelo usuário: as respostas não batem com a
realidade quando ele pergunta **como leigo**, sem conhecer os produtos.

## Conclusão

O motor é RAG documental aplicado a um problema de **catálogo estruturado**. RAG
documental responde bem "o que este documento diz sobre X". As perguntas reais
do negócio são de outra natureza — "quais produtos atendem densidade 35 e Shore
A 80", "quais produtos da linha rígidos estão ativos", "o ISO 13100 é usado em
algum FLEXX BT" — e essas são operações de filtro e junção sobre atributos, não
de similaridade de texto.

Não é necessário trocar o banco vetorial nem o modelo de embedding. O que muda
de patamar é **a unidade de indexação** (trecho → produto) e **onde mora o fato**
(texto → dado estruturado).

## O sintoma novo: a pergunta leiga pega o pior caminho do motor

O motor tem cinco caminhos de recuperação. Quatro exigem que quem pergunta já
fale a língua do acervo:

| Mecanismo | O que exige | Leigo tem? |
| :--- | :--- | :---: |
| `_detectar_codigos_produto` | código do produto ("AG 2032") | não |
| `_recuperar_por_secao` | nome de seção do boletim ("reatividade") | não |
| `_extrair_palavras_chave` | a palavra **literal** que está no boletim | não |
| detectores `_responder_*` | a formulação que alguém já corrigiu | não |
| busca vetorial `top_k=6` | nada | sim |

O usuário leigo cai sempre no último, que é o mais fraco do conjunto.

A causa é histórica e tem lógica: o motor foi construído corrigindo **perguntas
induzidas** — perguntas de quem conhece o catálogo. Os quinze commits `fix:`
entre as Sessões 50 e 57 ensinaram o motor a reconhecer esse vocabulário. Nada
disso foi construído para a pergunta de quem não conhece.

A busca por palavra-chave, que seria a rede de segurança, casa texto **literal**
no campo `content`. O usuário escreve "cola", o boletim diz *adesivo*; "colchão",
o boletim diz *espuma flexível de bloco*; "borracha", o boletim diz *elastômero*.
Nenhuma correspondência — e o caminho morre em silêncio, sem exceção e sem log.

## O erro era invisível para quem mais precisava percebê-lo

`obter_instrucao_template` injetava no prompt a ordem "OBRIGATÓRIO ... ESTRUTURE
SUA RESPOSTA FINAL **ESTRITAMENTE** SEGUINDO A ESTRUTURA DESTE TEMPLATE", e os
templates já vinham com o veredito preenchido: "Temos o produto ideal para sua
demanda!", "✅ Atende", "✅ Homologado", "✅ Compatível".

O modelo recebia seis trechos, possivelmente do produto errado, e uma instrução
obrigatória para preencher um formulário que **já afirmava que a busca deu
certo**. Não havia forma de responder "os trechos que recebi não sustentam isso"
sem desobedecer.

Quem conhece o catálogo percebia o erro na hora. Quem não conhece — o vendedor em
campo, que é o usuário-alvo — recebia uma resposta formatada, confiante e com
selos de ✅ sobre o produto errado. **O sistema tinha a mesma aparência acertando
e errando**, o que torna impossível perceber melhora ou piora.

## Outros achados de recuperação

1. **Chunk de 700 palavras dilui o vetor.** `chunk_text(text, 700, 120)`
   transforma um boletim inteiro em um ou dois vetores, cada um a média de
   "vantagens + armazenamento + reatividade + propriedades + aplicação". Como
   todos os boletins têm as mesmas seções e o mesmo texto de praxe, os vetores
   ficam próximos entre si e o que distingue o produto some na média.

2. **`top_k=6` fixo, sem reordenação e sem corte de score.** Num acervo de
   milhares de trechos, seis é o orçamento inteiro de evidência: uma resposta
   correta com doze produtos não cabe por construção. Os `scores` do Qdrant são
   descartados, então não há como separar "achei com confiança" de "peguei os
   seis menos ruins".

3. **A busca por palavra-chave usa `scroll`, que não ordena por relevância.**
   `client.scroll(..., limit=50)` percorre a coleção em ordem de ID — é
   paginação, não busca. Se trezentos trechos contêm "cortiça", voltam cinquenta
   arbitrários, e o trecho certo pode nunca entrar nos candidatos.

4. **A unidade de recuperação é o trecho, não o produto.** Um produto vive em
   vários arquivos (boletim, FISPQ, homologação) e vários trechos, e nada
   consolida isso. Uma pergunta que precisa cruzar aplicação, densidade e status
   só acerta por sorte de quais seis trechos caíram no contexto.

5. **Não existe identidade de produto no índice.** O payload da ingestão é
   `filename`, `filepath`, `chunk_index`, `content`, `sensivel`. Não há
   `product_id`, família, tecnologia, linha, status, natureza química,
   propriedades normalizadas, página ou revisão — tudo isso é re-derivado em
   tempo de consulta, por expressão regular sobre caminho e conteúdo. É a causa
   direta da série de commits `fix:`: cada um é um fato do catálogo sendo
   reconstruído por heurística. Com uma coluna `natureza = 'isocianato'`, o
   commit "exclui isocianatos da lista de elastômeros" não existiria.

6. **Nenhum teste mede acerto de recuperação.** São 45 arquivos de teste, todos
   sobre comportamento programado com dependências simuladas. Não há conjunto de
   perguntas com resposta esperada rodando contra o acervo real, então cada
   correção pode ter quebrado um caso anterior sem que ninguém saiba.

## O que foi implementado nesta sessão

### Bloco 1 — a apresentação segue o desfecho da análise

`backend/app/templates.py`. A instrução deixou de ser uma ordem incondicional de
preencher o template. O prompt agora exige classificar o desfecho **antes** de
escrever, entre quatro: candidato com evidência, comparação inconclusiva, nada
encontrado, falta informação decisiva. O template escolhido na tela governa
apenas o primeiro; os outros três têm formato fixo, em `FORMATOS_POR_DESFECHO`.

"Nada encontrado" é declarado no prompt como resposta **legítima e esperada** —
sem isso o modelo trata ausência de evidência como falha própria e inventa um
candidato para ter o que entregar.

O status de cada requisito passou de "✅ Atende" exemplificado sozinho para três
estados explícitos, incluindo "❓ Não informado no documento". Ausência de dado
não é aprovação nem reprovação — regra que já constava no `AGENT_SYSTEM_PROMPT`
e que o template contradizia na prática.

Toda afirmação técnica passa a carregar o arquivo de origem e o **trecho
literal** que a sustenta. É o que permite a quem não conhece o produto conferir
a resposta sozinho: lê o trecho citado e vê se ele fala do que foi perguntado.

Testes: `backend/tests/test_apresentacao_por_desfecho.py`. São asserções sobre o
prompt montado, que é a única superfície onde essa pressão existia. Não medem a
qualidade da resposta do LLM.

### Bloco 2 — tradução da pergunta leiga para o vocabulário do acervo

`backend/app/rag/query_expansion.py`. Uma chamada curta ao LLM antes da
recuperação traduz a pergunta para os termos que existem nos Boletins Técnicos
("cola" → "adesivo", "borracha" → "elastômero", "colchão" → "espuma flexível").

Quatro decisões deliberadas:

- **Só no caminho leigo.** A tradução só ocorre quando a pergunta não cita código
  de produto nem nome de seção. Nesses casos a recuperação exata já é mais
  confiável que qualquer sinônimo, e injetar termos traduzidos ali apenas
  diluiria um resultado que já está certo.
- **Os termos somam, nunca substituem.** A pergunta original continua sendo o
  critério; a tradução só amplia onde procurar. Os termos entram na busca
  textual e numa segunda busca vetorial, cujo resultado é mesclado sem duplicar.
- **Fail-open em dois níveis.** O módulo devolve lista vazia em qualquer falha, e
  o chamador em `engine.py` ainda envolve a chamada em `try/except`. O primeiro é
  promessa do módulo, o segundo é garantia do chamador: um recurso que existe
  para aumentar recall não pode derrubar uma recuperação que funcionaria sem ele.
- **Termo traduzido não é evidência.** `_montar_context_str` avisa o agente de
  quais termos foram usados e de que **não** são equivalência validada. Sem esse
  aviso, um boletim alcançado por "estofamento" seria apresentado como prova da
  aplicação "sofá" que o vendedor pediu, e a expansão viraria uma fonte nova de
  recomendação sem lastro.

Configuração em `.env`: `EXPANSAO_CONSULTA_ATIVA` (padrão `true`),
`EXPANSAO_CONSULTA_MODELO` (padrão: primeiro modelo da allowlist),
`EXPANSAO_MAX_TERMOS` (padrão 6). A chave de desligar existe para comparar o
motor com e sem tradução sobre as mesmas perguntas, sem reverter código.

Testes: `backend/tests/test_query_expansion.py` (módulo isolado) e
`backend/tests/test_query_expansion_no_retriever.py` (integração com o
retriever). Foi criado `backend/tests/conftest.py` que **desliga a tradução por
padrão em toda a suíte** — sem isso, dezenas de testes de recuperação sem relação
com o assunto passariam a fazer chamada de rede a cada execução.

Nenhum dos dois blocos mexe em ingestão, embedding ou payload. **Não é necessário
reindexar o acervo.**

## O que continua em aberto

Em ordem de retorno, e sem mudança em relação ao diagnóstico acima:

0. **Conjunto de avaliação e harness.** Perguntas reais com resposta esperada,
   rodando contra o acervo, reportando recall e regressão. É o que torna tudo o
   mais mensurável. O usuário é a persona certa para fornecer as perguntas
   leigas, mas não é quem pode dizer qual era a resposta certa — isso vem da
   Qualidade ou da Engenharia de Aplicação. O caminho natural é registrar as
   perguntas reais do uso (tabelas `feedback` e `conversation_messages` já
   existem) e um técnico marcar certo/errado periodicamente, em vez de alguém
   inventar perguntas induzidas.
1. **Catálogo estruturado no PostgreSQL**, preenchido por extração com LLM na
   ingestão, uma vez, offline, com o trecho de origem citado em cada campo.
   Elimina como classe tudo que virou `fix:` nas últimas semanas.
2. **Recuperação por produto**, com chunk por seção, reordenação e `top_k`
   contado em produtos, não em trechos.
3. **Plano tipado** no lugar da cascata de detectores `_responder_*`, cuja ordem
   hoje decide a resposta.
4. **Laço de ferramentas com duas ou três rodadas** — apontado no achado 5 da
   avaliação de 10/09 e ainda aberto: a chamada final não recebe `tools`, então
   o modelo não pode usar um resultado para decidir a próxima busca.

## Limites desta avaliação

Análise de código, configuração e histórico do repositório. Não houve ingestão,
migração nem alteração de dados. Os testes executados são unitários com
dependências simuladas: demonstram que o prompt deixou de forçar sucesso e que a
tradução entra e falha como projetado, **não** que a taxa de acerto sobre o
acervo real melhorou. Essa medição depende do item 0 acima e não existe hoje.
