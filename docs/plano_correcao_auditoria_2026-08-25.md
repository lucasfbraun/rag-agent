# Plano de correcao da auditoria - rascunho de tickets

Este e um rascunho local, nao publicado em issue tracker. O projeto nao possui `docs/agents/issue-tracker.md` nem tracker configurado para as Skills `to-spec`/`to-tickets`.

## Ordem proposta

1. **Bootstrap confiavel do banco**
   - Bloqueado por: nenhum.
   - Entrega: uma stack nova aplica migrations antes de aceitar trafego e um smoke test prova schema/login.

2. **Distinguir catalogo vazio de retrieval indisponivel**
   - Bloqueado por: nenhum.
   - Entrega: falhas de Qdrant/embedding produzem estado degradado/503 e nunca recomendacao silenciosa por conhecimento geral.

3. **Preservar pelo menos um Admin TI ativo**
   - Bloqueado por: nenhum.
   - Entrega: auto-rebaixamento e qualquer operacao que deixe zero admins ativos sao rejeitados, com testes concorrentes/transacionais.

4. **Validar a fronteira de `/api/match`**
   - Bloqueado por: nenhum.
   - Entrega: allowlist de modelos, schema `user|assistant`, limites de tamanho e rejeicao de papeis/campos arbitrarios.

5. **Corrigir e testar tool calling multiplo**
   - Bloqueado por: ticket 4.
   - Entrega: zero, uma e multiplas tools geram protocolo valido em todos os modelos suportados.

6. **Classificar e filtrar dados sensiveis no RAG**
   - Bloqueado por: ticket 2.
   - Entrega: chunks recebem classificacao e retrieval aplica o perfil antes de montar contexto; Vendedor nao ve custo/formula.

7. **Reconciliar o indice com o acervo**
   - Bloqueado por: ticket 2.
   - Entrega: arquivo alterado, reduzido, movido ou removido nao deixa chunks obsoletos; reexecucao segue retomavel.

8. **Validar modelo e dimensao vetorial antes da ingestao**
   - Bloqueado por: ticket 7.
   - Entrega: incompatibilidade aborta cedo, com diagnostico unico; falhas nao sao contabilizadas como conclusao bem-sucedida.

9. **Cobrir os seams RAG/ingestao/streaming**
   - Bloqueado por: tickets 2, 4, 5, 7 e 8.
   - Entrega: testes de integracao para retrieval, ingestao, tools e NDJSON, incluindo erros e regressões encontradas.

10. **Endurecer autenticacao e respostas de erro**
    - Bloqueado por: ticket 3.
    - Entrega: rate limiting, erros publicos redigidos e eventos de seguranca observaveis sem secrets.

11. **Corrigir reversibilidade do schema**
    - Bloqueado por: ticket 1.
    - Entrega: `upgrade -> downgrade -> upgrade` funciona em banco efemero, incluindo enums.

12. **Alinhar documentacao ao estado validado**
    - Bloqueado por: tickets 1 a 11.
    - Entrega: cronograma, progresso e README descrevem comandos, servicos, modelos, limites e evidencias atuais sem contradicoes.

## Decomposicoes que estavam misturadas

- “Idempotencia” misturou evitar duplicata com reconciliar alteracao/remocao; sao criterios distintos.
- “Campos sensiveis” misturou MCP estruturado com RAG nao estruturado; precisam de tickets separados.
- “Agente investigativo” misturou retrieval, estado conversacional, politica de recomendacao, tool calling e formatacao.
- “Ingestao em background” misturou autorizacao, execucao, observabilidade, concorrencia e resultado do job.
- “Fase 5 concluida” misturou entrega das nove tarefas com cumprimento integral da governanca.

Antes de publicar tickets em um tracker, confirmar granularidade, dependencias e se os tickets 6/7 exigem janela de reingestao dos 11.273 pontos.
