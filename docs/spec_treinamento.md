# Especificação — Treinamento do agente pelos usuários

**Status:** IMPLEMENTADO (backend e interface, 2026-09-11).
**Data:** 2026-09-10 (Sessão 38)
**Origem:** pedido do usuário — *"no perfil dizer se o mesmo tem permissão para 'treinar' nosso agente; a ideia é que o usuário consiga contribuir com o aprendizado e melhorar a qualidade das respostas"*. Perguntado entre três leituras possíveis, ele escolheu **as três**.

---

## 1. O que "treinar" NÃO é aqui

Vale começar pelo que este documento não propõe, porque a palavra "treinar" carrega uma expectativa que seria cara e errada neste contexto:

**Não é fine-tuning de modelo.** Ajustar pesos de um LLM exigiria milhares de exemplos, custo por rodada, e uma nova rodada a cada correção — com o efeito de que uma informação errada aprendida vira difícil de remover. Além disso, o agente troca de modelo por configuração (`gpt-4o-mini`, Gemini, Claude): um modelo ajustado prenderia o projeto a um fornecedor.

**O que é:** uma base de conhecimento curada pela equipe, recuperada junto com o acervo e injetada no contexto com precedência declarada. O aprendizado fica em **dado**, não em pesos — o que significa que ele é **legível, editável e removível**, e que uma correção errada se desfaz apagando uma linha.

---

## 2. As três modalidades

Elas não são variações do mesmo recurso: cada uma responde a uma pergunta diferente e entra no prompt em um lugar diferente.

### 2.1 Correção (`correcao`)

> *"Para esta pergunta, a resposta certa é aquela."*

**Origem:** o usuário viu uma resposta ruim e escreveu a correta. É a evolução natural do feedback útil/não útil: após marcar "não útil", a interface abre o formulário da correção. O feedback bruto permanece disponível para análise, mas não é injetado no prompt.

**Campos:** pergunta original, resposta que o agente deu (para auditoria), resposta correta, produto/código, aplicação/condição, fonte, autor e data.

**Papel no prompt:** a de maior precedência. Quando a pergunta atual é semanticamente próxima de uma correção registrada, ela entra como *"Para uma pergunta praticamente igual a esta, a equipe técnica já corrigiu a resposta para o seguinte"*.

**Risco específico:** uma correção casa por similaridade, e perguntas parecidas podem ter respostas diferentes por um detalhe (densidade, norma, aplicação). Mitigação: a correção **nunca substitui** a resposta — ela entra no contexto como evidência de alta prioridade, e o agente continua obrigado a citar a fonte e a checar se o caso é mesmo o mesmo.

### 2.2 Conhecimento (`conhecimento`)

> *"Isto é verdade sobre nossos produtos, e não está em nenhum boletim."*

**Origem:** regras e fatos que vivem na cabeça da equipe. Exemplos reais deste domínio: *"o CAT 136 substitui o CAT 90 desde 2025"*, *"nunca recomende a família AG para calçado"*, *"para cliente da cadeia de frios, considerar sempre isolamento térmico antes de densidade"*.

**Campos:** título, texto, palavras-chave/aplicação, autor, data, vigência opcional.

**Papel no prompt:** entra ao lado dos trechos do acervo, num bloco próprio rotulado como **conhecimento interno da equipe**, não como documento. A distinção importa: o agente precisa poder dizer *"segundo orientação interna"* em vez de atribuir isso a um boletim.

**Risco específico:** conhecimento interno pode **contradizer** um boletim. O agente não deve resolver a contradição sozinho nem escondê-la — deve apresentá-la ("o boletim diz X; há uma orientação interna de 2026 dizendo Y"). Quem decide é a pessoa.

### 2.3 Exemplo (`exemplo`)

> *"Responda perguntas assim desta forma."*

**Origem:** um par pergunta/resposta modelo. Serve a **formato, tom e nível de detalhe** — não a conteúdo.

**Campos:** pergunta exemplo, resposta exemplo, autor, data.

**Papel no prompt:** um bloco de *few-shot*, separado e explicitamente rotulado como **modelo de forma, não fonte de fato**. Sem essa marcação, o LLM copia os números do exemplo para a resposta real — é o modo de falha clássico de few-shot com conteúdo técnico.

**Risco específico:** exatamente esse vazamento de conteúdo. Mitigação: instrução explícita no prompt e, na tela, um aviso a quem cadastra de que os números do exemplo podem aparecer indevidamente — recomendar exemplos com valores genéricos.

---

## 3. Como um item de treinamento é recuperado

O mesmo problema do acervo: não dá para injetar tudo no prompt.

**Proposta:** os itens de treinamento são indexados no Qdrant, em **coleção separada** (`pu_treinamento`), com o mesmo modelo de embedding do acervo. Na consulta, além do `retrieve_products_context()` atual, roda uma busca nessa coleção e os melhores itens entram no contexto.

**Por que coleção separada e não a mesma:**
- O acervo é reconstruído por reingestão completa (aconteceu na Sessão 35c). Se o treinamento estivesse lá dentro, seria apagado junto — e ele não tem arquivo de origem para ser reindexado a partir de nada.
- Os filtros de listagem varrem a coleção inteira contando produtos (`catalog_stats`). Itens de treinamento entrariam nessas contagens como se fossem produtos.
- A precedência é diferente, e separar torna trivial dar pesos e limites distintos.

**Limite:** no máximo 2 correções, 3 conhecimentos e 2 exemplos por consulta, por similaridade. O corte mínimo é mais rigoroso para correções, e uma correção com produto definido é descartada quando a pergunta cita outro código. Sem teto, uma base de treinamento grande empurraria o acervo para fora do contexto — e o agente passaria a responder de memória curada em vez de documento.

---

## 4. Governança — as decisões que precisam da sua confirmação

Aqui é onde eu preciso de você. As opções abaixo mudam o sistema de formas relevantes.

### 4.1 Quem pode treinar, e com aprovação?

Uma permissão nova, `TRAIN_AGENT`, marcável por perfil como as outras. A pergunta é se o que ela produz entra direto.

| Opção | A favor | Contra |
|---|---|---|
| **A. Entra direto** | Rápido; o conhecimento chega no momento em que a pessoa lembra dele | Uma correção errada passa a valer para toda a equipe imediatamente |
| **B. Fila de aprovação** (como o upload) | Coerente com o item 3; erro não vira verdade sem revisão | Fricção; conhecimento pequeno pode não valer o ritual |
| **C. Direto, mas com autoria visível e remoção fácil** | Baixa fricção, rastro claro | O erro circula até alguém notar |

**Minha recomendação: B para `correcao` e `conhecimento`, A para `exemplo`.** A razão é a assimetria de dano: correção e conhecimento afirmam **fatos** que o agente vai repetir como verdade da empresa; exemplo só afeta **forma**, e uma forma ruim é visível na hora.

### 4.2 O que acontece quando o acervo é reindexado?

Nada — é o ponto da coleção separada. Mas vale registrar explicitamente que a reingestão **não** apaga treinamento, para ninguém se surpreender.

### 4.3 Um item de treinamento expira?

**Proposta: não automaticamente, mas com data visível.** Toda correção e conhecimento entram no prompt com a data (*"orientação interna registrada em 03/2026"*), para o agente e o leitor pesarem a idade. Expiração automática apagaria conhecimento ainda válido em silêncio.

### 4.4 Quem responde quando treinamento e acervo se contradizem?

**Proposta: ninguém — a contradição é apresentada.** O agente mostra os dois lados com as fontes e recomenda validação com P&D. Isso é coerente com os guardrails de evidência da Sessão 34, que já proíbem afirmar sem fonte.

---

## 5. O que já existe e vai ser reaproveitado

- **Feedback útil/não útil** (Sessão 32) — a correção nasce dele: um "não útil" ganha o botão *"escrever a resposta certa"*.
- **Fila de aprovação** (Sessão 38) — o mesmo padrão de pendente/aprovado/recusado com motivo, se a opção B for escolhida.
- **Perfis dinâmicos** (Sessão 37) — `TRAIN_AGENT` e `APPROVE_TRAINING` entram como permissões marcáveis, sem migration de perfil.
- **`get_embeddings` em lote** (Sessão 35c) — indexar um item de treinamento é uma chamada só.
- **Guardrails de evidência** (Sessão 34) — a instrução de não afirmar sem fonte já está no prompt e passa a valer também para o treinamento.

---

## 6. Implementação

1. [x] Tabela `itens_treinamento` + permissões `TRAIN_AGENT`/`APPROVE_TRAINING`.
2. [x] Serviço de CRUD + indexação na coleção `pu_treinamento`.
3. [x] Recuperação no `engine.py`, com os três blocos e a precedência declarada no prompt.
4. [x] Tela de cadastro, listagem e aprovação, incluindo a correção após feedback negativo.
5. [x] Escopo por produto/aplicação/fonte e bloqueio de código de produto divergente.
6. [x] Testes dos fluxos de recuperação, governança e interface.

O formulário aberto pelo feedback negativo exige `TRAIN_AGENT`. A mesma
permissão é validada novamente pelo endpoint de criação; `APPROVE_TRAINING`
isoladamente permite revisar a fila, mas não cadastrar uma correção.

**Estimativa de risco:** o passo 3 é o mais delicado. Mexer no prompt do agente já produziu regressão neste projeto, e a validação vai precisar de chamadas reais ao LLM comparando resposta antes/depois — não só teste de unidade.

---

## 7. Decisões adotadas (2026-09-10)

Tomadas por mim, na ausência de resposta às perguntas da seção 4, com a autorização explícita do usuário para executar. Ficam registradas para poderem ser revistas.

- [x] **4.1** — **correção e conhecimento passam por aprovação; exemplo entra direto.** A razão é a assimetria de dano: os dois primeiros afirmam FATOS que o agente repete como verdade da empresa, e um erro ali circula sem ninguém notar; exemplo afeta só a FORMA, e forma ruim é visível na primeira resposta.
- [x] **4.3** — **sem expiração automática, com data visível no prompt.** Expirar sozinho apagaria conhecimento ainda válido em silêncio; a data deixa o agente e o leitor pesarem a idade da orientação.
- [x] **Correção nasce dos dois caminhos** — do feedback negativo já existente (botão "escrever a resposta certa") e do cadastro direto, gravando na mesma tabela. O feedback é onde a correção ocorre naturalmente; o cadastro serve para quem já sabe o que quer registrar.
