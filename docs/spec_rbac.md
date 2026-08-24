# Especificação — RBAC & Governança (Fase 5)

Documento de referência para a implementação incremental da Fase 5 do `CRONOGRAMA.md`.
Fonte da matriz de perfis: `docs/proposta_do_projeto_similaridade.md`, seção 5 ("Matriz de Perfis e Permissões").

## Decisão de provisionamento (2026-08-24)

**Manual agora, desenho pronto para AD/LDAP depois.** Motivo: não há confirmação de que a empresa tem AD/LDAP disponível, nem credenciais, nem contato de TI envolvido — travar nisso paralisaria a Fase 5 indefinidamente. A autenticação é desenhada como uma peça substituível (ver "Estratégia de autenticação" abaixo), separada da autorização, para que um Adapter LDAP possa ser adicionado depois sem reescrever perfis/permissões/matriz.

## Política de senha (2026-08-24, tarefa 2)

Mínimo de 8 caracteres (`SenhaFracaError` em `backend/app/auth/security.py`), hash via `bcrypt`. Não há requisito de negócio documentado para regras adicionais (maiúscula/número/símbolo) — o mínimo de 8 é só uma defesa básica contra senha vazia/trivial, não uma política de segurança completa. Se o time de TI tiver uma política corporativa de senha, é pendência a levantar e ajustar aqui.

## Modelo de usuário

| Campo | Tipo | Justificativa |
|---|---|---|
| `id` | UUID (PK) | Identidade estável, independente do login (que pode mudar) |
| `username` | string, único | Login manual |
| `nome` | string | Identificação humana — auditoria ("quem gerou essa recomendação") |
| `email` | string, único | Contato; identificador natural corporativo; base para recuperação de senha futura |
| `password_hash` | string, nullable | Hash (bcrypt/argon2) — **nunca texto puro**. Nullable porque um usuário de origem `ldap` não deve ter senha armazenada aqui (a senha LDAP nunca é persistida na aplicação — exigência explícita do pedido) |
| `status` | enum (`ativo`/`inativo`) | Pedido explícito no escopo da Fase 5 |
| `perfil` | enum `Role` | Pedido explícito no escopo da Fase 5 |
| `origem` | enum (`manual`/`ldap`) | Permite ao sistema saber qual Adapter de autenticação usar para este usuário — a peça que viabiliza o modelo híbrido futuro sem migração de schema |
| `external_id` | string, nullable, **único** | Identificador no AD/LDAP, quando `origem = ldap`. Vazio para usuários manuais. Incluído agora (mesmo sem uso imediato) para não exigir migration nova quando o LDAP for integrado. Constraint de unicidade: um `external_id` (ex: `distinguishedName`/`objectGUID` do AD) identifica uma entrada única no diretório — permitir duplicata aqui abriria brecha para provisionar duas contas locais para a mesma identidade externa |
| `created_at` / `updated_at` | timestamp | Auditoria básica |

**Campos deliberadamente NÃO incluídos** (sem evidência de necessidade no escopo pedido): telefone, foto, departamento, cargo, hierarquia/gerente. Se algum desses for necessário, é pendência a levantar com o time, não uma suposição minha.

## Perfis (`Role`, enum)

Nomenclatura alinhada à proposta original (`docs/proposta_do_projeto_similaridade.md`):

- `VENDEDOR` (Vendedor / Representante)
- `TECNICO` (Técnico de Aplicação)
- `GESTOR` (Gestor Comercial)
- `QUIMICO_PD` (Químico / P&D)
- `ADMIN_TI` (Administrador TI)

**Decisão:** enum Python fixo, não tabela dinâmica. Não há evidência de que o negócio precise criar/editar perfis via UI — os 5 perfis vêm de uma decisão de negócio já documentada na proposta, não de configuração operacional. Se isso mudar, é decisão funcional nova, não uma correção desta spec.

## Estratégia de autorização (centralizada)

Não espalhar `if user.role == "..."` pelo código. Três peças, isoladas do resto da aplicação:

1. **`Permission`** (enum) — uma entrada por ação+recurso, ex: `VIEW_CATALOG`, `VIEW_HOMOLOGATION_FULL`, `VIEW_HOMOLOGATION_SUMMARY`, `MANAGE_TEMPLATES`, `SELECT_TEMPLATES`, `VIEW_COSTS`, `MANAGE_USERS`.
2. **`ROLE_PERMISSIONS`** (dict `Role -> set[Permission]`) — a fonte única da matriz de acesso (ver seção abaixo). Único lugar que precisa mudar se a matriz mudar.
3. **`require_permission(permission)`** — FastAPI dependency. Endpoints declaram a *permissão* exigida, não o perfil:
   ```python
   @app.get("/api/templates", dependencies=[Depends(require_permission(Permission.VIEW_CATALOG))])
   ```
   Isso mantém `main.py` sem saber como perfis mapeiam para permissões — essa lógica fica só em `ROLE_PERMISSIONS`.

`engine.py` (a lógica do agente RAG) permanece agnóstico de autorização — autorização é responsabilidade da camada HTTP (`main.py` + dependency), não do motor do agente. Exceção: ver "Campos sensíveis" abaixo, onde essa separação limpa esbarra numa limitação real do RAG atual.

## Campos sensíveis — e uma lacuna real encontrada na análise

A matriz da proposta define "Visualização de Custos" como o campo sensível central. **Achado da Etapa 1:** hoje **não existe nenhum campo de custo ou fórmula como dado estruturado** em lugar nenhum do código — nem no schema simulado do MCP (`pu_mcp_server.py`), nem como metadado no Qdrant. O que existe é texto livre extraído de Boletim Técnico, onde uma fórmula/relação de mistura pode aparecer misturada no meio do chunk.

Consequência prática, para não fingir resolver um problema que ainda não existe em dado real:

- **Camada estruturada (futura, Fase 4):** quando `consultar_catalogo_erp`/similares passarem a retornar dados reais de ERP com campo de custo, a proteção é trivial — filtrar o campo do dict de resposta antes de serializar, baseado em `require_permission(Permission.VIEW_COSTS)`. Isso é implementável e testável hoje, mesmo sem o campo real existir ainda (usar um campo de exemplo no simulado).
- **Camada RAG (hoje):** não há metadado que marque "este trecho contém informação de custo/fórmula" nos chunks já indexados (11.273 trechos). Filtrar por perfil ANTES de montar o contexto do LLM exigiria um campo estruturado na ingestão que não existe — **isto é uma pendência funcional real, não implementável nesta fase sem re-ingestão**, e fica registrada como tal, não escondida atrás de uma instrução de prompt (que não é proteção de backend de verdade).

**Quem pode ver o quê (não inventado — direto da matriz da proposta):**

| Campo/Recurso | Vendedor | Técnico | Gestor | Químico/P&D | Admin TI |
|---|---|---|---|---|---|
| Catálogo & TDS de linha | Total | Total | Total | Total | Total |
| Laudos de homologação | Sumarizado | Completo | Completo | Completo | Completo |
| Custos industriais | Bloqueado | **Pendência** (proposta diz "Opcional" — não define quem decide isso; ver Pendências) | Liberado | Liberado | Liberado |
| Fórmulas | Não definido explicitamente pela proposta — **pendência** | Pendência | Pendência (inferido: mesmo tratamento de custos, não confirmado) | Pendência | Pendência |

## Matriz de acesso (formato CRUD pedido)

A proposta original define acesso por **nível de detalhe do recurso**, não por CRUD genérico — a maioria dos recursos (Catálogo, Laudos) não tem semântica de "criar/editar/excluir" pelo usuário final (são gerados pela ingestão, não editados via UI). Reconciliando com o formato pedido, marcando N/A onde a ação não se aplica ao recurso (em vez de inventar):

| Recurso/Ação | Vendedor | Técnico | Gestor | Químico/P&D | Admin TI |
|---|---|---|---|---|---|
| Visualizar catálogo/TDS | Sim | Sim | Sim | Sim | Sim |
| Visualizar laudos de homologação | Sumarizado | Completo | Completo | Completo | Completo |
| Criar/Editar templates de resposta | Não (só seleciona) | Não (só seleciona) | Sim | Sim | Sim |
| Excluir templates | Não | Não | **Pendência** — proposta não distingue "editar" de "excluir" | Pendência | Sim (inferido de "Total") |
| Ver campos sensíveis (custos) | Não | Pendência | Sim | Sim | Sim |
| Gerenciar usuários | Não | Não | **Pendência** — proposta não menciona gestão de usuário por Gestor Comercial | Não | Sim |

## Pendências que dependem de decisão funcional (não resolvidas por mim)

1. O que exatamente significa "⚠️ Opcional" para Técnico ver custos — configurável por quem (Admin? por cliente/projeto)?
2. Regra de acesso a "fórmulas" — a proposta menciona o termo mas não define a matriz para ele separadamente de custos.
3. Se Gestor Comercial pode gerenciar usuários (a proposta diz "Acesso total" para Gestor, mas isso conflita com o `- [ ] Gerenciar usuários: não/não/definir/não/sim` sugerido no pedido do usuário, que implica só Admin TI). **Assumindo, até segunda ordem, que só Admin TI gerencia usuários** — é a leitura mais segura e mais alinhada ao pedido explícito do usuário nesta tarefa, mesmo a proposta original sendo ambígua nesse ponto específico.
4. Proteção de campos sensíveis dentro do conteúdo RAG (não estruturado) — pendência técnica real, não uma decisão de negócio, mas registrada aqui por afetar diretamente esta spec.
