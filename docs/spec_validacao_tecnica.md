# Validação técnica das respostas — medir o acerto por caminho do motor

Data: 18/09/2026. Fecha o item **0** de
[avaliacao_arquitetura_2026-09-18.md](avaliacao_arquitetura_2026-09-18.md),
"o que continua em aberto".

## O problema

Nenhum dos ~380 testes do projeto prova que a taxa de acerto sobre o acervo
real melhorou. Todos verificam comportamento programado com dependência
simulada. O usuário que opera o sistema é leigo no catálogo e só consegue
dizer "melhorou" por sensação — foi assim que se acumulou a série de quinze
commits `fix:` em que cada correção resolvia a queixa da vez e criava a
oposta, sem ninguém perceber.

Duas restrições definem a solução:

- **O usuário não pode ser quem valida.** Ele não conhece os produtos. Quem
  sabe a resposta certa é a Qualidade / Engenharia de Aplicação.
- **Ninguém inventa pergunta.** Pergunta induzida mede o quanto quem a
  inventou conhece o motor, não o quanto o motor serve a quem o usa. O
  conjunto de avaliação é o histórico de uso real.

## O ciclo

```
vendedor pergunta  →  resposta gravada com o CAMINHO que a produziu
                        ↓
                   técnico abre a fila e marca correta / incorreta / incompleta
                        ↓
                   relatório: taxa geral, taxa POR CAMINHO, lista de regressão
```

## 1. O que passou a ser persistido

`conversation_messages` ganhou duas colunas (migration `f1c93a7b2d45`):

| Campo | O que é | Por quê |
| :--- | :--- | :--- |
| `caminho` | qual mecanismo do motor atendeu | `model_used` agrupava **cinco** detectores sob `"catalogo-estruturado"` |
| `termos_busca` | palavras-chave + termos traduzidos | responde "a resposta errada veio de ter procurado a palavra errada?" |

`sources` já existia e é reaproveitado como "documentos citados" — não há
coluna nova para isso.

Os caminhos estão em `backend/app/rag/caminhos.py`, que é constante pura (sem
import de litellm/Qdrant, para o relatório e a CLI poderem lê-lo):
`escopo-deterministico`, `busca-reversa-produto`, `requisitos-compostos`,
`natureza-do-produto`, `classificacao-catalogo`, `aplicacao-com-evidencia`,
`conversacional-rag`, e `nao-registrado` para as respostas anteriores à
medição.

`model_used` **não mudou**: é o que a tela mostra e o que `feedback` já grava;
trocá-lo quebraria dado histórico sem ganho.

## 2. O veredito

Tabela `vereditos_tecnicos`. **Não** é extensão de `Feedback` nem de
`ItemTreinamento`, e a diferença é de conceito, não de conveniência:

- `Feedback` é o útil/não útil de **quem perguntou** — satisfação de um leigo.
  Somar os dois destruiria exatamente a distinção que dá valor ao veredito.
- `ItemTreinamento` **volta para o agente** (vai ao Qdrant e entra no contexto
  de respostas futuras). Veredito não pode ter esse destino: o projeto já teve
  um incidente com feedback bruto injetado no prompt (achado 2 de
  [avaliacao_agente_2026-09-10.md](avaliacao_agente_2026-09-10.md)). **Isto é
  medição, não memória** — nada aqui é lido em tempo de resposta.

  Nada impede que um veredito "incorreta" com resposta certa escrita vire
  **depois** um `ItemTreinamento` do tipo correção. É um segundo ato
  deliberado, com a aprovação que aquele fluxo já exige.

Três vereditos, não dois: **incompleta** é o desfecho mais comum deste motor
(cita um produto certo e omite cinco). Tratá-la como incorreta esconde que a
recuperação funcionou em parte; como correta, esconde o buraco de recall.

Pergunta, resposta, caminho, modelo e fontes são **copiados** no julgamento, e
a FK para a mensagem é `SET NULL`: a conversa pertence ao vendedor e ele pode
apagá-la; o conjunto de regressão não pode encolher em silêncio por causa
disso.

## 3. A permissão

`validate_answers`, semeada pela migration em **tecnico**, **quimico_pd** e
**admin_ti**. Vendedor e gestor ficam de fora — quem valida não é quem
pergunta. De lá em diante é editável na tela de perfis como qualquer outra.

Separada de `approve_training` porque são riscos de tamanhos diferentes:
aquela decide o que o agente vai **repetir como verdade**; esta só registra
medição.

## 4. O relatório

Endpoint `GET /api/validacao/relatorio` e
`python -m app.cli relatorio-validacao [--desde AAAA-MM-DD] [--formato json]`.

A **taxa por caminho** é o número que muda decisão. Se o determinístico acerta
90% e o conversacional 40%, "65% no geral" não diz a ninguém onde trabalhar. A
tabela vem ordenada do pior para o melhor, porque o relatório existe para
ordenar trabalho.

Honestidade estatística embutida:

- Sem histórico, o relatório **diz** que não há histórico, em vez de dar
  número. Não há dado sintético em lugar nenhum.
- Amostra abaixo de 5 por caminho (20 no geral) é marcada como insuficiente.
- Sem julgamento a taxa é `null`, não `0.0` — zero seria lido como "erra
  tudo", afirmação que o dado não faz.
- Dois técnicos que discordam tiram a resposta da taxa e a põem na regressão.
  Escolher um dos vereditos inventaria um consenso que não houve; divergência
  entre especialistas costuma significar pergunta ambígua, o que é informação
  sobre o acervo, não sobre o motor.

## 5. Como começar a rodar

1. **Aplicar a migration**: `alembic upgrade head` no container do backend.
2. **Conferir quem pode validar** em *Usuários e perfis → Perfis*. A migration
   já concede a `tecnico`, `quimico_pd` e `admin_ti`; se a pessoa da Qualidade
   usa outro perfil, marque "Validar tecnicamente respostas já dadas" nele.
3. **Deixar as pessoas usarem o agente por algumas semanas.** Só as respostas
   dadas a partir da migration carregam o caminho do motor. As anteriores
   podem ser validadas, mas entram como `nao-registrado` e não contam para a
   taxa por caminho.
4. **Sessão de validação**: o técnico entra em *Validar respostas → Fila*,
   filtra por "não útil" quando quiser começar pelo pior, e marca. Vinte
   respostas por sessão já produzem número.
5. **Ler o relatório** na aba *Relatório de acerto*, ou pela CLI. O que se
   olha primeiro é a primeira linha da tabela por caminho.
6. **Guardar o histórico**: `--formato json > relatorio-AAAA-MM-DD.json`. É a
   comparação entre duas semanas que diz se uma mudança foi melhora.

## Limites

O relatório mede o que os técnicos julgaram, não o acervo. Cobertura baixa
significa taxa pouco representativa, e o campo `cobertura` existe para que
isso fique visível em vez de implícito. A lista de regressão ainda não é
executada automaticamente contra o motor — ela é o insumo para isso, e
transformá-la em suíte de regressão executável é o próximo passo natural.
