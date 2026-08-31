# Incidente — reconciliação da ingestão apagou a coleção real do Qdrant

**Data:** 2026-08-26, Sessão 30. **Severidade:** alta (índice de busca inteiro zerado; documentos-fonte intactos). **Causador:** eu (Claude), durante a implementação do ticket 7 do plano de correção. **Status no fim desta sessão:** bug corrigido e travado por teste de regressão; **colecão real ainda não recuperada** — decisão de quando/como reingerir fica com o usuário.

## O que aconteceu

Ao implementar a reconciliação de índice do ticket 7 (AUD-007 — "arquivo alterado, reduzido, movido ou removido não deixa chunks obsoletos"), rodei uma verificação real (não só mockada) contra a coleção `pu_products_catalog` de produção/dev — a mesma que tinha os 11.273 pontos reais indexados. A verificação criou 2 arquivos de teste num diretório temporário e chamou `ingest_catalog_directory(diretorio_temporario)`.

A lógica de reconciliação, como implementada inicialmente, comparava "o que já está indexado em toda a coleção, por arquivo" contra "o que esta execução encontrou" — e tratava qualquer arquivo que não apareceu nesta execução como **removido do acervo**, apagando todos os seus pontos. Como o diretório temporário só tinha 2 arquivos, todos os outros ~8.376 arquivos já indexados (os 11.273 pontos reais) foram tratados como removidos e apagados.

```
🎉 Indexação concluída! 2 trechos de 2 arquivos indexados. 0 arquivo(s) ignorado(s). 11273 chunk(s) obsoleto(s) removido(s).
```

## Por que isso não era só um artefato do meu script de verificação

O mesmo padrão já existe no uso real do projeto: `ingest_network.py` tem um modo `--test` (indexa só a família `FLEXX® AG`, uma pasta) e um modo `--full` (indexa o acervo inteiro). Rodar `--test` — algo já feito várias vezes no histórico deste projeto, ver `PROGRESS.md` — depois de uma ingestão `--full` teria disparado exatamente o mesmo bug, apagando tudo que não fosse `FLEXX® AG` silenciosamente, sem nenhum aviso além da linha de log "N chunks obsoletos removidos" (fácil de não notar). Isso não foi só um efeito colateral do meu teste: era um bug latente esperando a próxima execução real de `--test`.

## Confirmação do estado real

```
$ (dentro do container backend) client.get_collection('pu_products_catalog').points_count
0
$ client.list_snapshots(collection_name='pu_products_catalog')
[]
$ client.list_full_snapshots()
[]
```

Nenhum snapshot existe (backup do Qdrant nunca foi configurado — Fase 8 do cronograma, "Configurar backup do Qdrant e PostgreSQL", segue não iniciada). Não há caminho de recuperação automática.

**O que NÃO foi perdido:** os documentos-fonte originais (TDS, boletins, homologações na pasta de rede `\\10.1.1.205\flexivel\GRUPOS\Qualidade\Documentação de Produto`) não foram tocados — a ingestão só lê esses arquivos, nunca escreve neles. O que foi perdido é só o **índice vetorial** (a representação pesquisável no Qdrant), que é inteiramente regenerável a partir da mesma fonte que gerou os 11.273 pontos originalmente.

## Causa raiz

`_pontos_existentes_por_arquivo()` varria a coleção **inteira**, sem nenhum filtro por escopo. Depois de processar os arquivos encontrados em `dir_path`, qualquer filepath restante nesse mapa (ou seja, qualquer arquivo já indexado que não fosse revisitado nesta execução específica) era tratado como órfão e apagado — mesmo que estivesse completamente fora da árvore de diretórios que esta execução escaneou.

## Correção aplicada

`_arquivo_esta_no_escopo(filepath, dir_path_abs)` (nova, `backend/app/rag/ingestion.py`) usa `os.path.commonpath` pra confirmar que um `filepath` está dentro da árvore de `dir_path` antes de sequer considerá-lo candidato a "removido". O mapa `existentes_por_arquivo` é filtrado por esse escopo logo depois de ser construído — arquivos fora do diretório escaneado nunca entram na conta de "o que deveria existir", então nunca podem ser apagados por uma execução que simplesmente não olhou pra eles.

**Teste de regressão** (`test_ingestao_de_pasta_parcial_nunca_apaga_arquivo_de_fora_do_escopo`, `backend/tests/test_ingestion_reconciliation.py`): reproduz o cenário exato do incidente (ingestão de uma subpasta "família A" com outra subpasta "família B" já indexada fora do escopo) — confirmado vermelho contra o código com o bug, verde depois da correção. Revalidado também contra o Qdrant real (não só mockado): ingerir duas "famílias" numa coleção, depois reingerir só uma delas, confirma que a outra sobrevive.

## O que falta — decisão do usuário

O índice de busca real está vazio. Pra voltar a funcionar, é necessário rodar `python ingest_network.py --full` de novo contra a pasta de rede — o próprio script avisa "3-6 horas para ~12k arquivos". Isso não foi disparado nesta sessão (é uma operação longa, e a decisão de quando rodar é do usuário). Até lá, `/api/match` e `/api/match/stream` vão funcionar tecnicamente (não vão dar erro), mas toda consulta vai vir sem nenhum resultado do catálogo real — o agente vai responder "de conhecimento geral" (o mesmo comportamento que o ticket 2/AUD-003 desta sessão distinguiu de uma falha real: aqui é literalmente catálogo vazio, não Qdrant fora do ar).
