# PU Matcher — Agente Investigativo e Consultivo de Match de Produtos (PU)

Agente RAG on-premise para apoiar vendas técnicas e engenharia de aplicação na indústria de poliuretanos,
localizando produtos já homologados no acervo da empresa a partir da demanda descrita pelo cliente.

Documentação de origem do projeto em [docs/](docs/):
- [Proposta do Projeto](docs/proposta_do_projeto_similaridade.md)
- [Guia Técnico do MVP](docs/guia_mvp_e_codigo_similaridade.md)

Acompanhamento do desenvolvimento:
- [CRONOGRAMA.md](CRONOGRAMA.md) — fases e marcos do projeto (fonte da verdade, versionada)
- [PROGRESS.md](PROGRESS.md) — log de progresso sessão a sessão (fonte da verdade, versionada)
- [Painel visual do projeto](https://claude.ai/code/artifact/91bf54cd-d88a-4816-abee-80f362863581) — dashboard com o mesmo conteúdo acima, republicado a cada avanço relevante

> **Nota:** o painel visual é uma página hospedada externamente no claude.ai (Claude Artifact) — **não existe como arquivo dentro deste repositório**. Ele é gerado a partir do conteúdo de `CRONOGRAMA.md`/`PROGRESS.md` e republicado no mesmo link acima sempre que esses arquivos forem atualizados. `CRONOGRAMA.md` e `PROGRESS.md` são a fonte da verdade; o painel é apenas um espelho visual de leitura.

## Stack

- **Backend:** FastAPI
- **RAG / Vector DB:** Qdrant
- **Multi-LLM:** LiteLLM (OpenAI, Gemini, Anthropic, Grok — ver `.env.example`)
- **Embedding:** `text-embedding-3-small` (OpenAI, 1536 dims). O modelo e o `VECTOR_SIZE` precisam ser os mesmos na ingestão e na consulta — trocar de embedding obriga a recriar a coleção do zero
- **Frontend:** Streamlit, atrás de um proxy reverso (Caddy) — ver "Identidade visual & instalação como app (PWA)"
- **Autenticação & RBAC:** PostgreSQL + JWT (Fase 5 — ver seção abaixo)
- **Ferramentas vivas:** MCP (catálogo ERP e normas/homologações — atualmente simuladas, ver Fase 4 do cronograma)

## Como rodar localmente

> **Use `docker compose` (com espaço), não `docker-compose` (com hífen).** O hífen é o Compose V1, descontinuado — e este `docker-compose.yml` **não funciona nele**: o arquivo não declara `version:`, então a V1 o lê como formato legado e ignora `healthcheck` e `depends_on: condition: service_healthy`, subindo os serviços fora de ordem.

1. Copie `.env.example` para `.env` e preencha as chaves de API reais (nunca commitar o `.env`):
   ```bash
   cp .env.example .env
   ```
2. Suba os contêineres (aguarda healthchecks automaticamente):
   ```bash
   docker compose up -d --build
   ```
3. Verifique se todos os serviços estão saudáveis:
   ```bash
   docker compose ps
   # ou via API:
   curl http://localhost:8000/api/health
   ```
4. Indexe documentos técnicos (coloque os arquivos em `data/raw_documents/` antes):
   ```bash
   # CLI (recomendado):
   docker exec -it pu_matcher_backend python -m app.cli ingest

   # Ou via API REST (roda em background):
   curl -X POST http://localhost:8000/api/ingest -H "Content-Type: application/json" -d '{"dir_path": "/app/data/raw_documents"}'
   ```
5. Crie o primeiro usuário Admin TI (obrigatório — sem ele ninguém consegue logar; ver "Autenticação & Perfis (RBAC)" abaixo para o comando).
6. Acesse a interface em `http://localhost:8501` e faça login com o usuário criado no passo anterior.
   > Desde a Sessão 27, `8501` é servido por um proxy Caddy na frente do Streamlit (serviço `proxy` no Compose), não pelo container `frontend` diretamente — necessário pro Service Worker do PWA funcionar (ver seção abaixo). Pra quem debuga direto no container, o Streamlit em si continua ouvindo em `8501` só na rede interna do Compose (sem porta publicada no host).

### Instalando em Ubuntu/Debian (servidor Linux)

O Ubuntu não traz o Docker. **Não** use as duas opções que o `apt` sugere quando `docker-compose` não é encontrado: `apt install docker-compose` instala o Compose V1 (descontinuado, incompatível com este arquivo, ver aviso acima), e `snap install docker` confina o daemon de um jeito que quebra os bind mounts usados aqui (`./data/qdrant_storage`, `./proxy/Caddyfile`).

Instale do repositório oficial:

```bash
sudo apt remove -y docker docker-engine docker.io containerd runc docker-compose
sudo apt update && sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER && newgrp docker   # rodar docker sem sudo
```

**Três coisas que pegam numa máquina nova, e não são erro do projeto:**

1. **Permissão do `.env`.** Se você criou o arquivo com `sudo`, ele fica de root e o Compose pode não conseguir lê-lo: `sudo chown $USER:$USER .env && chmod 600 .env`. Sem `SECRET_KEY` e `POSTGRES_PASSWORD` preenchidos, o backend levanta `RuntimeError` no import e o container entra em crash-loop — é proposital, não é falha.

2. **O banco vem vazio, inclusive sem usuários.** `data/qdrant_storage/` e `data/postgres_storage/` estão no `.gitignore`, então o clone traz só as pastas vazias: zero produtos indexados e **zero usuários**, ou seja, ninguém consegue nem fazer login. Crie o primeiro Admin TI com o comando da seção "Autenticação & Perfis (RBAC)" antes de tentar usar a interface.

3. **O acervo real está num compartilhamento SMB do Windows.** `ingest_network.py` aponta para `//10.1.1.205/flexivel/...`, um caminho UNC. No Linux é preciso montar o compartilhamento antes (`sudo apt install cifs-utils` e um `mount -t cifs`), e ajustar o caminho no script.

## Como rodar localmente (sem Docker)

Útil para desenvolvimento e debug sem precisar do Docker Desktop.

```bash
# Instalar dependências
pip install -r requirements.txt

# Terminal 1 — Backend
python backend/run_local.py

# Terminal 2 — Frontend
python frontend/run_local.py
```

A interface estará em `http://localhost:8501` e o backend em `http://localhost:8000`.
> **Qdrant:** Instale e rode localmente (`docker run -p 6333:6333 qdrant/qdrant`) ou aponte `QDRANT_HOST` para um servidor remoto.
> **PWA:** rodando assim (sem o proxy Caddy do Docker Compose), o card "Instalar PU Matcher" não aparece — o Service Worker só é servido na raiz (`/`) através do proxy. Isso é o comportamento esperado em dev local.

## Autenticação & Perfis (RBAC)

Desde a Fase 5, toda funcionalidade de negócio (chat/match, templates, ingestão) exige login — `/` e `/api/health` continuam públicos (liveness/monitoramento). Autorização é decidida sempre no backend, nunca confiando só na interface.

**5 perfis** (`Vendedor`, `Técnico`, `Gestor Comercial`, `Químico/P&D`, `Admin TI`), cada um com um conjunto fixo de permissões. Referência completa da matriz de acesso, decisões de design e pendências funcionais em [docs/spec_rbac.md](docs/spec_rbac.md).

**Provisionamento é manual** (sem AD/LDAP integrado ainda — desenho já pronto pra isso, ver spec). Não existe auto-cadastro nem uma segunda conta Admin TI por padrão: o primeiro usuário precisa ser criado direto no banco, uma única vez, por quem tem acesso ao servidor:

```bash
docker exec -i pu_matcher_backend python <<'PYEOF'
from app.db import SessionLocal
from app.auth.user_service import create_user
from app.models import Role

session = SessionLocal()
create_user(
    session,
    username="admin",
    nome="Nome Completo",
    email="admin@suaempresa.com.br",
    password="TROQUE-ESTA-SENHA",   # mínimo 8 caracteres
    perfil=Role.ADMIN_TI,
)
session.commit()
session.close()
print("Usuário Admin TI criado.")
PYEOF
```

> Use um heredoc como acima (não `-c "..."` com a senha inline) para a senha não aparecer no histórico do shell/`docker inspect`. Depois de logado, o próprio Admin TI cria os demais usuários via API (`POST /api/auth/users`, ver tabela abaixo) — não precisa repetir esse passo manual.
>
> **Débito conhecido:** não existe hoje um comando de CLI dedicado pra esse bootstrap (`python -m app.cli create-admin` ou similar) — o script acima é o único caminho. Ver `docs/spec_rbac.md`, "Pendências".

**Fluxo de login:**
```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "sua-senha", "manter_conectado": true}'
# -> {"access_token": "...", "token_type": "bearer", "expires_in_seconds": 2592000}

curl http://localhost:8000/api/match -H "Authorization: Bearer <access_token>" ...
```

Na tela de login, **Manter conectado** preserva o acesso no navegador depois
que ele é fechado. A sessão lembrada dura 30 dias por padrão, configurável por
`REMEMBER_ME_EXPIRE_DAYS`; sem marcar, o token continua com a validade normal de
`ACCESS_TOKEN_EXPIRE_MINUTES` e fica apenas na sessão atual. Ao reabrir, o
frontend valida `/api/auth/me`, portanto token expirado e usuário desativado não
entram. O botão **Sair** remove também a credencial persistida.

### Cadastrando usuários pela tela

Quem tem perfil **Admin TI** vê o atalho **"Usuários e perfis"** na barra lateral. Ali dá para cadastrar quem vai acessar o sistema, definir o perfil de cada um, editar dados, redefinir senha e desativar/reativar contas — sem precisar de CLI.

Desativar **não apaga** a conta: o histórico de conversas e o feedback da pessoa são preservados, e a conta pode ser reativada depois. O sistema também recusa desativar o último Admin TI ativo, e ninguém consegue desativar a própria conta.

> A tela é a interface de `/api/auth/users`; a autorização continua sendo decidida no backend (`Permission.MANAGE_USERS`). O primeiro Admin TI ainda precisa ser criado pelo comando descrito acima — é ele quem cadastra os demais.

### Cadastrar um usuário direto do Active Directory

Com AD configurado, a aba **Cadastrar usuário** abre num seletor: *Active Directory* (padrão) ou *Senha local*. No modo AD você procura a pessoa no diretório, clica em **Selecionar**, e o formulário já vem com login, nome e e-mail preenchidos — só falta escolher o perfil. **Não há campo de senha**: quem entra pelo AD não tem senha local.

É o caminho preferencial quando existe diretório: os dados já estão lá, e redigitá-los só cria oportunidade de erro de grafia — nome divergente entre os dois sistemas atrapalha auditoria depois. Os campos continuam editáveis, porque o `displayName` do AD às vezes traz cargo ou setor junto do nome.

Se a conta do AD não tiver e-mail preenchido (acontece com contas de serviço), o sistema pede um: `email` é obrigatório e único aqui.

### Vincular um usuário já existente ao Active Directory

Quando há AD configurado, o cartão de cada usuário (em "Usuários e perfis") ganha a seção **Active Directory**: procure a pessoa pelo nome, login ou e-mail e clique em **Vincular**. A partir daí ela entra no PU Matcher com a **senha da rede**.

**Vincular apaga a senha local**, e isso é o ponto principal, não um efeito colateral: manter a senha local viva daria à pessoa duas credenciais válidas — a TI desligaria a conta no AD achando que cortou o acesso, e ela continuaria entrando aqui com a senha antiga.

Outras regras que o servidor aplica (a tela apenas as comunica):

- Conta **desabilitada** no AD não pode ser vinculada, e deixa de autenticar se for desabilitada depois — sem depender de alguém lembrar de desativar nos dois lugares.
- Uma conta do AD só pode ser vinculada a **um** usuário (`external_id` é único).
- **Desvincular exige definir a senha local no mesmo passo**: usuário de origem LDAP tem `password_hash` nulo, e desvincular sem senha o deixaria sem forma nenhuma de entrar.
- Desativar o usuário **aqui** corta o acesso mesmo que ele siga ativo no AD.

O vínculo é gravado pelo **`objectGUID`**, não pelo `distinguishedName` nem pelo `sAMAccountName`. Os dois últimos mudam — o DN quando a pessoa é movida de OU, o login quando alguém é renomeado. O `objectGUID` é imutável, e o login do AD é resolvido a partir dele a cada autenticação: **renomear alguém no AD não quebra o login aqui.**

O que o AD **não** decide: perfil e permissões. A conta continua sendo criada e governada no PU Matcher; o diretório só responde "esta senha é desta pessoa?". Amarrar perfil a grupo do AD prenderia a autorização da aplicação à estrutura de OUs da TI, que muda por motivos alheios a este sistema.

Configuração em `.env` (todas opcionais — sem `LDAP_SERVER` o recurso simplesmente não aparece):

```bash
LDAP_SERVER=10.1.1.205
LDAP_DOMAIN=empresa.local
LDAP_BASE_DN=DC=empresa,DC=local
LDAP_BIND_USER=conta.servico@empresa.local
LDAP_BIND_PASSWORD=...        # a conta precisa apenas de LEITURA no diretório
LDAP_PORT=636                 # LDAPS. Em 389 a senha trafega em TEXTO CLARO
LDAP_USE_SSL=true
LDAP_VALIDAR_CERTIFICADO=false
```

> **Débito consciente:** `LDAP_VALIDAR_CERTIFICADO=false` é o padrão porque AD corporativo costuma usar certificado de CA interna, ausente do truststore do container. Isso protege contra escuta passiva, **não** contra man-in-the-middle dentro da rede. Ligue a validação quando a CA interna estiver instalada na imagem do backend.

### Perfis e permissões

Na mesma área de administração, aba **Perfis e permissões**. Perfis não são fixos: você cria, edita e exclui pela tela, sem migration nem deploy.

O que você marca são **permissões** — cada uma corresponde a algo que o servidor realmente verifica. A lista de permissões possíveis vem do código (uma permissão só existe porque algum endpoint a checa); o que é dado é quem tem o quê. *Administrador do sistema* é a permissão que libera a própria tela de administração.

O sistema **impede que você se tranque para fora**. Não é possível excluir o perfil que administra, tirar a permissão de administração do último perfil que a tem, mover o último administrador para outro perfil, nem desativá-lo. A checagem conta **usuários ativos que administram** — um perfil com a permissão e sem ninguém ativo não abre a porta de ninguém.

Os cinco perfis originais (`vendedor`, `tecnico`, `gestor`, `quimico_pd`, `admin_ti`) são marcados como *do sistema*: não podem ser excluídos, mas as permissões deles continuam editáveis. O identificador (`slug`) de qualquer perfil não muda depois de criado — ele é a referência estável usada por integração e pelos testes.

> **Perfis criados por você não recebem permissões novas automaticamente.** Quando uma versão acrescenta uma permissão (upload, treinamento), a migration só a concede aos cinco perfis originais. Só você sabe o que um perfil que você criou deveria poder fazer.

### Enviar documentos para o acervo

Quem tem a permissão *Enviar documentos* pode anexar PDF, Word ou TXT no próprio
campo do chat e também vê o atalho **Documentos** na barra lateral. O clipe de
anexo não aparece para perfis sem essa permissão, e o endpoint repete a mesma
validação. É possível enviar vários arquivos de uma vez; o texto digitado junto
vira a observação que o aprovador recebe. O arquivo **não entra direto**: ele
fica numa fila até alguém com *Aprovar documentos* revisar.

A fila existe porque o acervo é a fonte que o agente cita como verdade para toda a equipe. Um PDF errado entrando sozinho contamina as respostas de todo mundo, e o estrago só aparece quando alguém desconfia de uma recomendação — muito depois, e sem ligação óbvia com o upload.

- **Formatos aceitos:** PDF, Word e texto. **Imagens e PDFs digitalizados são recusados** — sem OCR o texto não é extraível, e o arquivo não acrescentaria nada às respostas. *(OCR é pendência conhecida.)*
- **Limite:** 25 MB por arquivo.
- **A observação importa:** é o que o aprovador lê para decidir. Sem ela, ele recebe um PDF sem contexto.
- **Recusar exige motivo** — senão quem enviou reenvia o mesmo arquivo.
- **Aprovar tem volta:** um documento aprovado pode ser removido do acervo depois, e só os trechos dele saem do índice.

### Treinar o agente

Quem tem a permissão *Treinar o agente* vê o atalho **Treinar agente** na barra
lateral. Também pode marcar uma resposta como não útil e escrever a correção
diretamente abaixo da resposta. Correções ficam pendentes até alguém com
*Aprovar treinamento* revisar; só então podem influenciar novas respostas.
Sem *Treinar o agente*, o usuário ainda pode avaliar a resposta como não útil,
mas o formulário de correção não é exibido e a API recusa qualquer tentativa de cadastro.
Ter somente *Aprovar treinamento* também não permite criar correções; essa
permissão libera apenas a revisão da fila.

Quem tem a permissão *Treinar o agente* pode registrar conhecimento que melhora as respostas. **Não é fine-tuning:** o aprendizado fica em dado, o que o torna legível, editável e removível apagando uma linha — e mantém o projeto livre para trocar de modelo de IA.

Três modalidades, com papéis diferentes:

| Modalidade | Para quê | Aprovação |
|---|---|---|
| **Correção** | O agente respondeu errado e você escreve a resposta certa | sim |
| **Conhecimento** | Um fato ou regra que a equipe sabe e não está em boletim nenhum | sim |
| **Exemplo** | Um par pergunta/resposta modelo — ensina a **forma** de responder | não |

Correção e conhecimento passam por aprovação porque afirmam **fatos** que o agente vai repetir como verdade da empresa. Exemplo afeta só a forma, e forma ruim é visível na primeira resposta.

O que o agente faz com cada um:

- **Correção** tem prioridade sobre a formulação dele — mas ele confere se o caso é mesmo o mesmo, porque perguntas parecidas podem diferir em densidade, norma ou aplicação.
- **Conhecimento** é apresentado como *orientação interna*, com a data, nunca como se fosse conteúdo de um boletim.
- **Exemplo** vale só como modelo de forma: os números dele não são usados como fato.
- Se uma orientação interna **contradisser um boletim**, o agente mostra os dois lados com as fontes e encaminha para a equipe técnica — não escolhe sozinho.

O treinamento fica numa coleção separada do acervo, então **reindexar o acervo não apaga o que a equipe ensinou**.

### Evidência para aplicações

Quando a pergunta pede produtos para uma aplicação, o agente só apresenta um
candidato se o Boletim Técnico do próprio produto mencionar aquela aplicação.
Uma categoria próxima não serve como comprovação: por exemplo, documentos sobre
colchões ou sobre o setor automotivo não comprovam uso em assento de ônibus.
FISPQ e certificado também não são usados para essa decisão.

Pedidos claros por aplicação são consultados diretamente no catálogo, sem
embedding e sem o LLM escolher sinônimos. Se não houver menção explícita, a
resposta informa que nenhum boletim foi encontrado. Equivalências técnicas só
devem ser adicionadas depois de validação da equipe técnica/P&D.

Quando a pergunta também contém valores técnicos, o agente cruza todos os
requisitos em uma única consulta. O mesmo Boletim Técnico precisa comprovar a
aplicação e cada especificação; atender apenas à densidade, dureza ou tempo não
basta. Propriedades específicas permanecem separadas — por exemplo, **densidade
por imersão** não é tratada como densidade livre ou aparente. Esse caminho
estruturado aceita frases naturais como `no mínimo 200Kg/m³ de densidade por
imersão` e não depende do LLM para fazer a interseção.

## Estrutura do projeto

```
├── docker-compose.yml
├── Dockerfile.backend
├── Dockerfile.frontend
├── requirements.txt
├── .env.example
├── backend/app/
│   ├── main.py             # API REST FastAPI
│   ├── templates.py        # Templates padronizados de resposta
│   ├── db.py, models.py    # Postgres: usuários, feedback e conversas
│   ├── conversation_*.py   # CRUD e persistência do histórico por usuário
│   ├── auth/                # Autenticação, autorização e administração de usuários (Fase 5)
│   ├── mcp/                 # Ferramentas MCP (catálogo ERP, normas)
│   └── rag/                 # Ingestão e motor do agente investigativo
│       ├── engine.py        # Recuperação híbrida, prompt do agente e guardrails
│       ├── catalog_stats.py # Listagem/contagem por nome, família, aplicação e tipo
│       ├── spec_search.py   # Busca por VALOR de especificação técnica (ver seção abaixo)
│       └── doc_sections.py  # Seções do boletim (vantagens, reatividade, embalagens…)
├── backend/alembic/        # Migrations do banco relacional (Fase 5)
├── frontend/app.py          # Interface de chat Streamlit (com tela de login + card de PWA)
├── frontend/static/         # manifest.json, service-worker.js e ícone do PWA (Sessão 27)
├── .streamlit/config.toml   # Tema (paleta da marca) + enableStaticServing
├── proxy/Caddyfile          # Proxy reverso — serve o Service Worker em "/" (ver seção PWA)
├── IDENTIDADE_VISUAL.md     # Guia de paleta/tipografia da marca (origem: projeto FIDC) + aplicação aqui
├── data/raw_documents/      # TDS, catálogos e homologações (não versionado)
└── docs/                    # Documentos originais da proposta, guia técnico e spec_rbac.md
```

## O que o agente consegue responder

O PU Matcher responde somente sobre o catálogo e o domínio técnico de
poliuretanos da empresa. Pedidos claramente alheios a esse escopo, como receitas
culinárias, previsão do tempo, placares e piadas, são recusados antes da busca no
Qdrant e antes da chamada ao modelo. Por exemplo, "como fazer um bolo de
cenoura?" recebe uma explicação curta do escopo e não uma resposta de
conhecimento geral. O filtro usa intenções inequívocas e preserva aplicações
técnicas válidas, como "molde de bolo com poliuretano".

O acervo é consultável por **cinco caminhos diferentes**, e o agente escolhe pelo formato da pergunta. Isso importa porque cada um falha nos casos dos outros — busca semântica pura, por exemplo, nunca acerta uma pergunta sobre número.

| Tipo de pergunta | Exemplo | Como é resolvido |
|---|---|---|
| **Produto/documento nomeado** | "traga o boletim do AG 2032" | Busca híbrida: match exato do código no nome do arquivo + busca semântica (`rag/engine.py`) |
| **Relação entre produtos** | "o AG 2032 é utilizado no CAT 136?" | Busca os documentos de cada produto e prioriza referências cruzadas nos dois sentidos (`rag/engine.py`) |
| **Aplicação, tipo ou família** | "produtos para colchão", "quais são as colas", "produtos da família CAT" | Varredura do acervo por nome e por conteúdo, em blocos separados (`rag/catalog_stats.py`) |
| **Valor(es) de especificação técnica** | "hidroxila de 180", "densidade abaixo de 32 kg/m³ e pega livre abaixo de 220 s" | Leitura estruturada da tabela, varredura do acervo e interseção dos requisitos (`rag/spec_search.py`) |
| **Seção do boletim** | "quais as vantagens do AG 2032", "como armazenar", "vem em tambor?", "qual a validade" | Detecção da seção pedida, filtro de recuperação dentro do produto e instrução explícita no contexto (`rag/doc_sections.py`) |

### Busca por especificação técnica

Perguntas com **nome de propriedade + número** não são respondíveis por busca vetorial: o embedding não compara grandezas, então "hidroxila 180" e "hidroxila 34" geram vetores quase idênticos e o agente responderia com o valor errado sem dar sinal disso. Por isso essa consulta tem caminho próprio:

- **20 propriedades canônicas** — índice de hidroxila, teor de NCO, viscosidade, densidade, dureza, pH, teor de sólidos, teor de água, índice de acidez, funcionalidade, relação de trabalho, tempos de creme/reação/gel/pega/cura/desmolde, resiliência, alongamento, resistência à tração e ao rasgo, temperatura.
- **Operadores** — valor aproximado (tolerância padrão de ±5%), `acima de`, `abaixo de` e `entre X e Y`. Tempos são convertidos para segundos na leitura e na pergunta ("2 minutos" → 120 s) e reexibidos em linguagem de chão de fábrica ("1 min 21 s").
- **Limites conservadores** — para afirmar atendimento a `abaixo de`, o máximo da faixa do produto precisa ficar dentro do limite; para `acima de`, o mínimo precisa ficar dentro; para `entre`, a faixa inteira precisa estar contida. Sobreposição parcial não é apresentada como atendimento.
- **Vários requisitos** — todos são interpretados e verificados na mesma varredura. O resultado é a interseção por produto; propriedade ausente significa requisito não comprovado.
- **Todos os produtos compatíveis** — recomendações por vários requisitos não usam a prévia de dez itens. Se mais de um produto comprovar simultaneamente todas as condições, todos são apresentados com suas evidências.
- **Varredura completa, não top-k** — a pergunta é "todos os produtos com hidroxila 180"; um punhado de trechos daria uma contagem errada com cara de certa.
- **Evidência sempre junto** — cada resultado traz a faixa lida, a unidade, o trecho literal e o documento de origem, com o tipo marcado: Boletim Técnico é a especificação de referência, Certificado/Laudo vale para o lote analisado.
- **Quando nada casa**, a resposta traz a faixa daquela propriedade em todo o acervo, para o agente dizer "não há produto com hidroxila 180; o acervo vai de 20 a 415 mgKOH/g" em vez de um "não encontrei" seco.

Consultas com uma especificação são injetadas diretamente no contexto. Quando há dois ou mais requisitos, a resposta é formatada pelo próprio mecanismo estruturado, sem chamada ao LLM: ele não pode omitir um critério, prometer uma busca posterior nem acrescentar produtos fora da interseção.

> **Leitura estruturada da tabela:** a extração de PDF embaralha as colunas do boletim ("Índice de hidroxilas mgKOH/g 54,0 – 58,0 56,80 Viscosidade Brookfield a 25 °C cPs 7500 – 8500 7810"), e o LLM troca número, unidade e propriedade nesse texto. Todo contexto recuperado leva junto a leitura já resolvida propriedade→valor dos mesmos documentos — sem tirar o texto bruto, que o agente precisa poder conferir.

## APIs disponíveis

| Endpoint | Método | Autenticação | Descrição |
|---|---|---|---|
| `/` | GET | Pública | Health check simples |
| `/api/health` | GET | Pública | Status detalhado (Qdrant + coleção) |
| `/api/auth/login` | POST | Pública | Login — devolve o token JWT |
| `/api/auth/me` | GET | Login | Confirma o token e devolve o usuário dono dele |
| `/api/templates` | GET | `SELECT_TEMPLATE` (todos os perfis) | Lista os templates disponíveis |
| `/api/match` | POST | `VIEW_CATALOG` (todos os perfis) | Executa o agente investigativo |
| `/api/match/stream` | POST | `VIEW_CATALOG` (todos os perfis) | Mesma coisa, em streaming (SSE/NDJSON) |
| `/api/conversations` | GET / POST | `VIEW_CATALOG` (todos os perfis) | Lista / cria conversas do usuário atual |
| `/api/conversations/{id}` | GET / DELETE | `VIEW_CATALOG` (proprietário) | Retoma / apaga uma conversa e suas mensagens |
| `/api/ingest` | POST | `MANAGE_INGESTION` (só Admin TI) | Dispara ingestão de documentos em background |
| `/api/auth/users` | GET / POST | `MANAGE_USERS` (só Admin TI) | Lista / cria usuário |
| `/api/auth/users/{id}` | GET / PATCH | `MANAGE_USERS` (só Admin TI) | Obtém / edita usuário |
| `/api/auth/users/{id}/password` | POST | `MANAGE_USERS` (só Admin TI) | Redefine a senha de um usuário |
| `/api/auth/users/{id}/deactivate` | POST | `MANAGE_USERS` (só Admin TI) | Desativa usuário ("excluir" nunca apaga a linha) |

Matriz completa de quem tem cada permissão em [docs/spec_rbac.md](docs/spec_rbac.md).

## CLI de Ingestão

```bash
# Verificar saúde do sistema
docker exec -it pu_matcher_backend python -m app.cli health

# Indexar documentos
docker exec -it pu_matcher_backend python -m app.cli ingest
docker exec -it pu_matcher_backend python -m app.cli ingest --dir /app/data/raw_documents
```

## Backup (Qdrant + Postgres)

```bash
# do host (nao de dentro de um container), com a stack rodando via docker compose
python backup.py                  # Qdrant + Postgres
python backup.py --qdrant-only
python backup.py --postgres-only
python backup.py --manter 30      # retencao (default: 14 backups mais recentes de cada tipo)
```

Gera um snapshot da coleção do Qdrant (`data/backups/qdrant/`, via API HTTP do próprio Qdrant) e um `pg_dump` do Postgres (`data/backups/postgres/`, via `docker exec` no container — o binário `pg_dump` só existe lá). Nenhum dos dois é commitado (`data/backups/` no `.gitignore`). Instruções de restauração no docstring de `backup.py`. **Execução manual** — ainda não agendado automaticamente (Windows Task Scheduler, a configurar quando houver servidor de produção definido, ver `CRONOGRAMA.md` Fase 8). Motivação: `docs/incidente_2026-08-26_reingestao_apagou_colecao.md` — sem isso, o incidente que apagou a coleção real não tinha nenhum caminho de recuperação automática.

## Identidade visual & instalação como app (PWA)

A paleta e a tipografia vêm de [IDENTIDADE_VISUAL.md](IDENTIDADE_VISUAL.md) (originalmente escrito pro projeto FIDC, em Next.js/Tailwind) — a seção final desse documento ("Aplicação no PU Matcher") explica onde cada cor vive aqui: `.streamlit/config.toml` para os widgets nativos, CSS injetado em `frontend/app.py` pro resto (tipografia Roboto, cards).

**Marca:** os arquivos oficiais do Grupo Flexível foram aplicados em 2026-09-09, substituindo o monograma placeholder. Os originais em alta resolução ficam em `frontend/static/brand/` e os derivados de web são gerados por script:

```bash
python frontend/static/gerar_assets_marca.py
```

| Arquivo | Onde aparece |
|---|---|
| `logo.png` (427×120) | topo da tela de login — logo horizontal, com o nome |
| `icon-192.png` | sidebar (o símbolo "X", onde não cabe o nome) |
| `favicon.png` (64×64) | aba do navegador e atalho do app |
| `icon-192/512.png` | ícones do PWA — os dois tamanhos que o Chrome exige para considerar o app instalável |
| `icon-maskable-512.png` | versão com 20% de folga, para o recorte circular do Android não cortar o símbolo |

Os derivados ficam versionados porque a imagem do frontend não tem Pillow; rode o script novamente se os originais mudarem. O ícone original é 4191×4500 (quase quadrado, mas não exatamente) — o script centraliza numa tela quadrada em vez de redimensionar direto, que distorceria a marca.

**Instalar como app (PWA):** a tela de login mostra um card compacto "Instalar aplicativo" somente quando o navegador permite (Chrome/Edge desktop ou Android, critérios de instalabilidade atendidos). Isso exigiu um proxy reverso (Caddy, serviço `proxy` no Compose) na frente do Streamlit — o Service Worker precisa ser servido em `/` pra controlar a página inteira, e o Streamlit só serve estático em `/app/static/*`. Sem o proxy (ex: `frontend/run_local.py`) ou quando a instalação não está disponível, o card fica oculto e não ocupa espaço na tela.

> **Não testado em navegador real** — este ambiente não tem Chrome/Chromium disponível pra automação. O que foi verificado: os arquivos (`manifest.json`, os 3 PNGs de ícone, `service-worker.js`) são servidos com o `Content-Type` e no caminho certos através do proxy (`docker compose up` + `curl`), e a suíte `frontend/tests/test_pwa_assets.py` trava se alguém quebrar essa forma no futuro. O comportamento de instalação em si (o Chrome de fato mostrar o prompt) precisa de verificação manual num navegador real antes de considerar a Fase 6 fechada.

## Status

🟨 **Reingestão em andamento (recuperando do incidente de 2026-08-26).** A coleção real do Qdrant foi apagada durante o desenvolvimento (Sessão 30, detalhe completo em `docs/incidente_2026-08-26_reingestao_apagou_colecao.md`); os documentos-fonte na pasta de rede não foram tocados, só o índice. `python ingest_network.py --full` está rodando (disparado 2026-08-31, 3-6h esperadas) — validar a contagem final contra os 11.273 pontos históricos quando terminar. Backup configurado desde 2026-08-31 (ver seção acima) para que uma futura perda de índice tenha caminho de recuperação, o que não existia no incidente original.

Fases 0 (Setup) e 5 (RBAC & Governança — 9/9 tarefas, autenticação/autorização/administração de usuários funcionando de ponta a ponta) concluídas. Fase 1 (Ingestão) recuperando (ver acima) — o código de ingestão/reconciliação está pronto (ver auditoria abaixo). Fase 2 (motor RAG/agente investigativo) em andamento, com gaps de comportamento já identificados. Fase 6 (Frontend/UX de Campo) iniciada — identidade visual da marca aplicada e card de instalação como PWA (ver seção acima), ainda sem validação em navegador real nem no logo definitivo. Fase 8 com o item de backup adiantado (ver seção acima); os demais itens seguem não iniciados, assim como as Fases 3, 4 e 7. Ferramentas de ERP/normas ainda simuladas (Fase 4).

Auditoria de qualidade de código em andamento desde 2026-08-25 — 9 de 12 bugs já corrigidos. Ver `docs/auditoria_2026-08-25.md`, `docs/verificacao_auditoria_2026-08-26.md` e `docs/plano_correcao_auditoria_2026-08-25.md` para bugs confirmados e plano de correção.

Estado completo, sessão a sessão, em [PROGRESS.md](PROGRESS.md); visão geral por fase em [CRONOGRAMA.md](CRONOGRAMA.md).
