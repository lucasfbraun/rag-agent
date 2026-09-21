#!/usr/bin/env python3
"""
Diagnóstico de extração do acervo — quanto do acervo real NUNCA foi indexado.

POR QUE ESTE SCRIPT EXISTE
--------------------------
`app.rag.ingestion.ingest_catalog_directory` descarta arquivos em silêncio:
quando `extract_text_from_file` devolve texto vazio, o arquivo vira `+1` em
`skipped_files` e só aparece num `print` do resumo final, que ninguém guarda.
`indexar_arquivo` faz o equivalente levantando `ValueError`. Resultado: o
tamanho do buraco entre "acervo" e "acervo indexado" nunca foi medido.

Um boletim escaneado, uma FISPQ digitalizada e uma homologação que veio por fax
caem exatamente aí. Se forem uma fração relevante do acervo, isso explica mais
falhas de resposta do que qualquer ajuste de recuperação — nenhum trabalho de
retrieval vale mais do que descobrir isso antes.

O QUE ELE FAZ (E O QUE NÃO FAZ)
-------------------------------
Varre a árvore do acervo, extrai texto com EXATAMENTE a mesma
`extract_text_from_file` da ingestão e classifica cada arquivo. NÃO indexa,
NÃO gera embedding, NÃO abre conexão com o Qdrant, NÃO escreve nada dentro do
acervo. Só leitura, mais o CSV opcional no caminho que o usuário indicar.

A extração é a mesma de propósito: uma heurística paralela ("este PDF parece
ter texto") responderia sobre um pipeline que não existe. O número só vale se
refletir o que a ingestão de fato faz.

Os mesmos filtros de deduplicação da ingestão são aplicados ANTES de contar
(`_filtrar_duplicatas_de_formato`, `_ordenar_por_prioridade`, hash de
conteúdo), senão o total não corresponde ao que seria indexado de verdade.

A CLASSIFICAÇÃO É O PONTO
-------------------------
"1.200 arquivos falharam" não ajuda ninguém a decidir. "900 são escaneados,
250 são .doc legado, 50 são corrompidos" aponta três ações diferentes — OCR,
conversão em lote, resgate manual pela Qualidade.

USO
---
    python tools/diagnostico_extracao.py                       # acervo inteiro (padrão de rede)
    python tools/diagnostico_extracao.py /mnt/acervo --limite 300
    python tools/diagnostico_extracao.py /mnt/acervo --csv problemas.csv
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# --- Saída UTF-8 (mesmo motivo de ingest_network.py: sem isto o console do
# Windows quebra em qualquer nome de arquivo com acento, e o acervo inteiro é
# escrito em português). `hasattr` porque sob pytest o stdout capturado não
# tem reconfigure.
for _fluxo in (sys.stdout, sys.stderr):
    if hasattr(_fluxo, "reconfigure"):
        try:
            _fluxo.reconfigure(encoding="utf-8")
        except Exception:
            pass

_RAIZ_PROJETO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND = os.path.join(_RAIZ_PROJETO, "backend")
if os.path.isdir(_BACKEND) and _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# `app.config` levanta RuntimeError no import sem POSTGRES_PASSWORD/SECRET_KEY,
# e `app.rag.ingestion` importa `app.config`. Rodando no host (fora do
# container, que recebe essas variáveis pelo env_file) o .env do projeto
# resolve. Onde nem .env existe, um valor de fachada mantém o diagnóstico
# utilizável: ele não conecta no PostgreSQL nem assina token nenhum — só
# precisa que o módulo importe.
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(_RAIZ_PROJETO, ".env"))
except Exception:
    pass
for _var in ("POSTGRES_PASSWORD", "SECRET_KEY"):
    if not os.getenv(_var):
        os.environ[_var] = "diagnostico-somente-leitura"

from pypdf import PdfReader  # noqa: E402

from app.rag.catalog_stats import _produto_do_filepath  # noqa: E402
from app.rag.ingestion import (  # noqa: E402
    _filtrar_duplicatas_de_formato,
    _hash_conteudo,
    _ordenar_por_prioridade,
    chunk_text,
    extract_text_from_file,
)

# Pasta de rede do acervo real. Barra normal, não invertida — mesmo motivo
# documentado em ingest_network.py (Git Bash/MSYS mexe na string antes de ela
# chegar no Python quando são barras invertidas).
ACERVO_PADRAO = "//10.1.1.205/flexivel/GRUPOS/Qualidade/Documentação de Produto"

# As quatro extensões que `ingest_catalog_directory` sequer olha. Qualquer
# outra coisa na árvore (.xls, .jpg, .msg, .dwg) nunca chegou perto do índice,
# e isso também é resposta — por isso o censo por extensão inclui tudo.
EXTENSOES_SUPORTADAS = (".pdf", ".docx", ".doc", ".txt")

# Assinatura OLE2 — o formato do Word 97-2003 (.doc binário). `python-docx` só
# abre OOXML (um zip, assinatura "PK"), então todo arquivo com esta assinatura
# é perda garantida na ingestão de hoje, resolvível em lote com LibreOffice.
_ASSINATURA_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ASSINATURA_ZIP = b"PK"

# `chunk_text` só guarda trechos com mais de 40 caracteres. Um arquivo que
# extrai menos que isso passa pela extração e mesmo assim não gera NADA
# indexado — é uma falha silenciosa a mais, não um sucesso.
_MINIMO_PARA_VIRAR_CHUNK = 40

# Acima de 40 e abaixo daqui, o arquivo vira um único chunk raso — o PDF que
# extrai só o cabeçalho e devolve meia dúzia de palavras. Ele entra no índice,
# ocupa uma vaga do `top_k` e nunca é um bom resultado de busca. Não é erro de
# extração, é qualidade de extração; por isso conta separado.
LIMIAR_TEXTO_POBRE = 200

# --- Status ---------------------------------------------------------------
# Aproveitáveis
OK = "OK"
OK_POBRE = "OK_TEXTO_POBRE"
# Falhas (nada é indexado)
PDF_ESCANEADO = "PDF_ESCANEADO"
PDF_PROTEGIDO = "PDF_PROTEGIDO"
DOC_LEGADO = "DOC_LEGADO"
ARQUIVO_VAZIO = "ARQUIVO_VAZIO"
SEM_CHUNK = "TEXTO_CURTO_DEMAIS"
CORROMPIDO = "CORROMPIDO"
ERRO_DESCONHECIDO = "ERRO_DESCONHECIDO"
# Descartados antes de extrair (não são falha — a ingestão também os descarta)
DUPLICATA_FORMATO = "DUPLICATA_DE_FORMATO"
DUPLICATA_CONTEUDO = "DUPLICATA_DE_CONTEUDO"
NAO_SUPORTADO = "EXTENSAO_NAO_SUPORTADA"

STATUS_APROVEITAVEIS = (OK, OK_POBRE)
STATUS_DE_FALHA = (
    PDF_ESCANEADO,
    PDF_PROTEGIDO,
    DOC_LEGADO,
    ARQUIVO_VAZIO,
    SEM_CHUNK,
    CORROMPIDO,
    ERRO_DESCONHECIDO,
)
STATUS_DESCARTADOS = (DUPLICATA_FORMATO, DUPLICATA_CONTEUDO, NAO_SUPORTADO)

ACAO_POR_STATUS = {
    PDF_ESCANEADO: "OCR (ocrmypdf/tesseract) — o documento existe, só não tem camada de texto",
    PDF_PROTEGIDO: "remover a proteção no arquivo original (Qualidade) e reindexar",
    DOC_LEGADO: "converter em lote para .docx/.pdf (LibreOffice --convert-to)",
    ARQUIVO_VAZIO: "conferir se o arquivo perdeu conteúdo no disco; provavelmente excluir",
    SEM_CHUNK: "arquivo quase sem texto: verificar se é capa/carimbo — OCR ou descarte",
    CORROMPIDO: "resgatar o original; o arquivo no acervo não abre",
    ERRO_DESCONHECIDO: "investigar caso a caso (ver coluna detalhe do CSV)",
    OK_POBRE: "extração rasa: conferir se o PDF é texto real ou só cabeçalho sobre imagem",
}


@dataclass
class ResultadoArquivo:
    """O que uma varredura descobriu sobre um arquivo."""

    caminho: str
    status: str
    caracteres: int = 0
    chunks: int = 0
    paginas: Optional[int] = None
    tamanho_bytes: int = 0
    detalhe: str = ""
    produto: Optional[str] = None
    # Preenchido só para arquivos aproveitáveis. Guardado aqui pra a
    # deduplicação por conteúdo não precisar extrair o texto uma segunda vez:
    # a extração é o passo caro da varredura, e reextrair dobraria as horas.
    hash_conteudo: Optional[str] = None

    @property
    def aproveitavel(self) -> bool:
        return self.status in STATUS_APROVEITAVEIS


def _extensao(caminho: str) -> str:
    return os.path.splitext(caminho)[1].lower()


def _primeiros_bytes(caminho: str, quantos: int = 8) -> bytes:
    try:
        with open(caminho, "rb") as f:
            return f.read(quantos)
    except Exception:
        return b""


def _e_doc_binario_legado(caminho: str) -> bool:
    """True se o arquivo é Word 97-2003 (OLE2), que `python-docx` não abre.

    Vale pra `.doc` e também pra `.docx` de nome mentiroso — renomear a
    extensão é prática comum em acervo antigo e não converte nada."""
    return _primeiros_bytes(caminho).startswith(_ASSINATURA_OLE2)


def _paginas_do_pdf(caminho: str) -> Tuple[Optional[int], Optional[bool], str]:
    """(nº de páginas, está criptografado, erro). O nº pode ser None se o PDF
    não abrir — e aí o erro diz por quê.

    A criptografia é consultada ANTES das páginas de propósito: num PDF
    protegido, `len(reader.pages)` já levanta exceção, e perder o
    `is_encrypted` nesse ponto faria o arquivo cair como "corrompido" — que
    manda a Qualidade resgatar um original que está intacto, só trancado."""
    criptografado: Optional[bool] = None
    try:
        leitor = PdfReader(caminho)
        criptografado = bool(getattr(leitor, "is_encrypted", False))
        return len(leitor.pages), criptografado, ""
    except Exception as e:
        return None, criptografado, f"{type(e).__name__}: {e}"


def _classificar_texto_vazio(caminho: str, tamanho: int) -> ResultadoArquivo:
    """A extração funcionou e devolveu texto em branco. Por quê?

    Esta é a pergunta que a ingestão nunca fez: ela conta tudo isso junto em
    `skipped_files`. PDF com páginas e sem camada de texto é escaneado e OCR
    resolve; PDF sem páginas é corrompido e OCR não resolve nada; DOCX sem
    parágrafo é arquivo vazio de verdade. Três ações diferentes."""
    ext = _extensao(caminho)

    if tamanho == 0:
        return ResultadoArquivo(caminho, ARQUIVO_VAZIO, tamanho_bytes=tamanho, detalhe="0 bytes")

    if ext == ".pdf":
        paginas, criptografado, erro = _paginas_do_pdf(caminho)
        if paginas is None:
            return ResultadoArquivo(
                caminho, CORROMPIDO, tamanho_bytes=tamanho,
                detalhe=f"PDF não abre para contagem de páginas ({erro})",
            )
        if criptografado:
            return ResultadoArquivo(
                caminho, PDF_PROTEGIDO, paginas=paginas, tamanho_bytes=tamanho,
                detalhe="PDF criptografado; texto inacessível sem a senha",
            )
        if paginas > 0:
            return ResultadoArquivo(
                caminho, PDF_ESCANEADO, paginas=paginas, tamanho_bytes=tamanho,
                detalhe=f"{paginas} página(s), nenhuma com camada de texto — OCR resolveria",
            )
        return ResultadoArquivo(
            caminho, CORROMPIDO, paginas=0, tamanho_bytes=tamanho,
            detalhe="PDF abre mas não tem nenhuma página",
        )

    if ext in (".doc", ".docx") and _e_doc_binario_legado(caminho):
        return ResultadoArquivo(
            caminho, DOC_LEGADO, tamanho_bytes=tamanho,
            detalhe="Word 97-2003 (OLE2); python-docx não lê este formato",
        )

    return ResultadoArquivo(
        caminho, ARQUIVO_VAZIO, tamanho_bytes=tamanho,
        detalhe="extração devolveu texto em branco",
    )


def _classificar_falha_de_extracao(caminho: str, erro: Exception, tamanho: int) -> ResultadoArquivo:
    """A extração levantou exceção. Traduz a exceção em ação possível."""
    ext = _extensao(caminho)
    descricao = f"{type(erro).__name__}: {erro}"

    if ext in (".doc", ".docx"):
        if _e_doc_binario_legado(caminho):
            return ResultadoArquivo(
                caminho, DOC_LEGADO, tamanho_bytes=tamanho,
                detalhe=f"Word 97-2003 (OLE2); python-docx não lê este formato — {descricao}",
            )
        if not _primeiros_bytes(caminho, 2).startswith(_ASSINATURA_ZIP):
            return ResultadoArquivo(
                caminho, CORROMPIDO, tamanho_bytes=tamanho,
                detalhe=f"não é OOXML (zip) nem OLE2 — {descricao}",
            )
        return ResultadoArquivo(caminho, CORROMPIDO, tamanho_bytes=tamanho, detalhe=descricao)

    if ext == ".pdf":
        paginas, criptografado, _ = _paginas_do_pdf(caminho)
        if criptografado:
            return ResultadoArquivo(
                caminho, PDF_PROTEGIDO, paginas=paginas, tamanho_bytes=tamanho,
                detalhe=f"PDF criptografado — {descricao}",
            )
        return ResultadoArquivo(
            caminho, CORROMPIDO, paginas=paginas, tamanho_bytes=tamanho, detalhe=descricao
        )

    return ResultadoArquivo(caminho, ERRO_DESCONHECIDO, tamanho_bytes=tamanho, detalhe=descricao)


def classificar_arquivo(caminho: str) -> ResultadoArquivo:
    """Classifica UM arquivo usando a extração real da ingestão.

    Nunca levanta: um arquivo que estoure uma exceção inesperada vira
    ERRO_DESCONHECIDO, não derruba uma varredura de horas."""
    try:
        tamanho = os.path.getsize(caminho)
    except Exception:
        tamanho = 0

    if _extensao(caminho) not in EXTENSOES_SUPORTADAS:
        return ResultadoArquivo(
            caminho, NAO_SUPORTADO, tamanho_bytes=tamanho,
            detalhe="a ingestão nem olha esta extensão",
        )

    try:
        texto = extract_text_from_file(caminho)
    except Exception as erro:  # inclui PDF quebrado, docx não-zip, permissão
        return _classificar_falha_de_extracao(caminho, erro, tamanho)

    if not texto or not texto.strip():
        return _classificar_texto_vazio(caminho, tamanho)

    limpo = texto.strip()
    chunks = chunk_text(texto)
    # Contar páginas reabre e reparseia o PDF. Só vale a pena quando o texto
    # é pouco: "38 caracteres em 12 páginas" é o retrato de um escaneado com
    # carimbo; num arquivo que rendeu texto de sobra, a página não muda ação.
    pouco_texto = len(limpo) < LIMIAR_TEXTO_POBRE
    paginas = (
        _paginas_do_pdf(caminho)[0]
        if _extensao(caminho) == ".pdf" and pouco_texto
        else None
    )

    if not chunks:
        return ResultadoArquivo(
            caminho, SEM_CHUNK, caracteres=len(limpo), chunks=0, paginas=paginas,
            tamanho_bytes=tamanho,
            detalhe=(
                f"{len(limpo)} caractere(s): abaixo dos {_MINIMO_PARA_VIRAR_CHUNK} "
                "que chunk_text exige — extrai e mesmo assim não indexa nada"
            ),
        )

    status = OK_POBRE if pouco_texto else OK
    detalhe = (
        f"só {len(limpo)} caractere(s) em {len(chunks)} trecho(s): vira chunk raso"
        if status == OK_POBRE
        else ""
    )
    return ResultadoArquivo(
        caminho, status, caracteres=len(limpo), chunks=len(chunks), paginas=paginas,
        tamanho_bytes=tamanho, detalhe=detalhe, hash_conteudo=_hash_conteudo(texto),
    )


# --- Seleção de arquivos --------------------------------------------------


def listar_arquivos(raiz: str) -> List[str]:
    """Todos os arquivos da árvore. `os.walk` em vez do `glob` da ingestão
    porque `glob(**/*.*)` ignora arquivo sem ponto no nome — irrelevante pra
    indexar, relevante pra um censo que precisa ser completo."""
    encontrados: List[str] = []
    for pasta, _subpastas, arquivos in os.walk(raiz, onerror=lambda e: None):
        for nome in arquivos:
            encontrados.append(os.path.join(pasta, nome))
    return encontrados


def selecionar_arquivos(todos: Sequence[str]) -> Tuple[List[str], List[str], List[str]]:
    """(a varrer, descartados por formato, não suportados).

    Reproduz, na ordem, o que `ingest_catalog_directory` faz antes de extrair
    qualquer texto: filtra extensão, descarta o par PDF/DOCX do mesmo
    documento e ordena deixando as pastas "restaurado" por último, pra elas
    perderem o desempate de conteúdo idêntico."""
    suportados = [f for f in todos if _extensao(f) in EXTENSOES_SUPORTADAS]
    nao_suportados = [f for f in todos if _extensao(f) not in EXTENSOES_SUPORTADAS]
    mantidos, descartados_formato = _filtrar_duplicatas_de_formato(suportados)
    return _ordenar_por_prioridade(mantidos), descartados_formato, nao_suportados


def amostrar(arquivos: Sequence[str], limite: Optional[int]) -> List[str]:
    """Amostra espaçada uniformemente, não os N primeiros.

    Os N primeiros de uma lista ordenada por caminho são uma família só de
    produto — a amostra diria mais sobre a pasta "FLEXX AG" do que sobre o
    acervo. O passo fixo atravessa a árvore inteira e é determinístico, o que
    deixa duas execuções comparáveis."""
    if not limite or limite <= 0 or limite >= len(arquivos):
        return list(arquivos)
    passo = len(arquivos) / limite
    return [arquivos[int(i * passo)] for i in range(limite)]


# --- Varredura ------------------------------------------------------------


@dataclass
class Varredura:
    resultados: List[ResultadoArquivo] = field(default_factory=list)
    total_na_arvore: int = 0
    extensoes: Counter = field(default_factory=Counter)
    duplicatas_formato: int = 0
    nao_suportados: int = 0
    amostrada: bool = False
    interrompida: bool = False
    segundos: float = 0.0


def varrer(
    arquivos: Sequence[str],
    intervalo_progresso: int = 50,
    escrever_progresso=None,
) -> List[ResultadoArquivo]:
    """Classifica cada arquivo, deduplicando por hash de conteúdo como a
    ingestão faz — o primeiro a aparecer fica, os idênticos seguintes são
    DUPLICATA_DE_CONTEUDO e não contam como arquivo perdido nem como ganho."""
    resultados: List[ResultadoArquivo] = []
    hashes_vistos: Dict[str, str] = {}
    total = len(arquivos)
    inicio = time.monotonic()

    for indice, caminho in enumerate(arquivos, start=1):
        try:
            resultado = classificar_arquivo(caminho)
        except Exception as erro:
            # `classificar_arquivo` promete não levantar. Se a promessa
            # falhar, a varredura de horas continua mesmo assim.
            resultado = ResultadoArquivo(
                caminho, ERRO_DESCONHECIDO,
                detalhe=f"falha inesperada na classificação: {type(erro).__name__}: {erro}",
            )

        assinatura = resultado.hash_conteudo
        if resultado.aproveitavel and assinatura:
            if assinatura in hashes_vistos:
                resultado = ResultadoArquivo(
                    caminho, DUPLICATA_CONTEUDO, caracteres=resultado.caracteres,
                    tamanho_bytes=resultado.tamanho_bytes,
                    detalhe=f"conteúdo idêntico a {hashes_vistos[assinatura]}",
                )
            else:
                hashes_vistos[assinatura] = caminho

        resultado.produto = _produto_do_filepath(caminho)
        resultados.append(resultado)

        if escrever_progresso and (indice % intervalo_progresso == 0 or indice == total):
            escrever_progresso(indice, total, time.monotonic() - inicio)

    return resultados


def _progresso_no_terminal(indice: int, total: int, decorridos: float) -> None:
    """Sinal de vida. Sem isso, numa varredura de milhares de arquivos lenta
    por extração de PDF, o usuário conclui que travou e mata o processo."""
    ritmo = indice / decorridos if decorridos > 0 else 0
    restante = (total - indice) / ritmo if ritmo > 0 else 0
    linha = (
        f"  {indice}/{total} arquivos ({100 * indice / total:.1f}%) — "
        f"{ritmo:.1f} arq/s — faltam ~{restante / 60:.0f} min"
    )
    if sys.stdout.isatty():
        print(f"\r{linha:<78}", end="", flush=True)
        if indice == total:
            print()
    else:
        print(linha, flush=True)


# --- Relatório ------------------------------------------------------------


def produtos_sem_documento(resultados: Sequence[ResultadoArquivo]) -> Tuple[List[str], List[str]]:
    """(produtos cegos, produtos com pelo menos 1 documento aproveitável).

    É o número que decide: não "arquivos perdidos", mas produtos que o agente
    é incapaz de encontrar, faça o que fizer com a recuperação."""
    aproveitaveis: Dict[str, bool] = defaultdict(bool)
    for r in resultados:
        if r.produto and r.status not in (NAO_SUPORTADO,):
            aproveitaveis[r.produto] = aproveitaveis[r.produto] or r.aproveitavel
    cegos = sorted(p for p, tem in aproveitaveis.items() if not tem)
    servidos = sorted(p for p, tem in aproveitaveis.items() if tem)
    return cegos, servidos


def cegos_por_duplicata(resultados: Sequence[ResultadoArquivo]) -> List[str]:
    """Produtos cegos cujo acervo se resume a arquivos idênticos a outros já
    contados.

    O texto desses produtos ESTÁ indexado — sob o caminho do arquivo vencedor,
    que é de outra pasta. O produto some da contagem por pasta, mas mandar
    esses arquivos para OCR não resolveria nada: não é falha de extração, é
    documento repetido no acervo. Separar os dois evita encher a fila de OCR
    com trabalho inútil."""
    cegos, _ = produtos_sem_documento(resultados)
    conjunto = set(cegos)
    com_duplicata = {
        r.produto for r in resultados
        if r.produto in conjunto and r.status == DUPLICATA_CONTEUDO
    }
    return sorted(com_duplicata)


def _percentis(valores: Sequence[int]) -> Dict[str, int]:
    if not valores:
        return {}
    ordenados = sorted(valores)

    def p(fracao: float) -> int:
        # round, não int: truncar faz o p90 de uma lista de 2 cair no índice 0
        # e o relatório sair com "p90 menor que a mediana", que destrói a
        # confiança no resto dos números.
        indice = min(round(fracao * (len(ordenados) - 1)), len(ordenados) - 1)
        return ordenados[int(indice)]

    return {
        "min": ordenados[0],
        "p10": p(0.10),
        "mediana": int(statistics.median(ordenados)),
        "p90": p(0.90),
        "max": ordenados[-1],
    }


def _linha_pct(rotulo: str, quantidade: int, total: int, largura: int = 34) -> str:
    pct = (100 * quantidade / total) if total else 0.0
    return f"  {rotulo:<{largura}} {quantidade:>7}  ({pct:5.1f}%)"


def montar_relatorio(v: Varredura, raiz: str) -> str:
    L: List[str] = []
    A = L.append
    resultados = v.resultados
    total = len(resultados)

    A("=" * 78)
    A("DIAGNÓSTICO DE EXTRAÇÃO DO ACERVO")
    A("=" * 78)
    A(f"Pasta:    {raiz}")
    A(f"Duração:  {v.segundos / 60:.1f} min")
    if v.interrompida:
        A("ATENÇÃO:  varredura INTERROMPIDA — os números abaixo são parciais.")
    if v.amostrada:
        A(
            f"ATENÇÃO:  AMOSTRA de {total} arquivos espaçados pela árvore, não o acervo "
            "inteiro.\n          As porcentagens valem; os números absolutos, não."
        )

    A("")
    A("1. O QUE EXISTE NA ÁRVORE")
    A("-" * 78)
    A(f"  Arquivos na árvore inteira: {v.total_na_arvore}")
    for ext, qtd in v.extensoes.most_common():
        marca = "" if ext in EXTENSOES_SUPORTADAS else "   <- a ingestão nem olha"
        A(f"  {ext or '(sem extensão)':<20} {qtd:>7}{marca}")
    A("")
    A(f"  Descartados por duplicata de formato (mesmo nome/pasta): {v.duplicatas_formato}")
    A(f"  Extensão não suportada:                                  {v.nao_suportados}")
    A(f"  Efetivamente candidatos à ingestão:                      {total}")

    por_status = Counter(r.status for r in resultados)
    aproveitaveis = [r for r in resultados if r.aproveitavel]
    falhas = [r for r in resultados if r.status in STATUS_DE_FALHA]
    duplicatas_conteudo = por_status.get(DUPLICATA_CONTEUDO, 0)
    base = len(aproveitaveis) + len(falhas)

    A("")
    A("2. QUANTO RENDE TEXTO")
    A("-" * 78)
    A(_linha_pct("Aproveitáveis (viram chunk)", len(aproveitaveis), base))
    A(_linha_pct("Perdidos (nada é indexado)", len(falhas), base))
    A(f"  {'Duplicatas de conteúdo ignoradas':<34} {duplicatas_conteudo:>7}  (não são perda)")

    A("")
    A("3. POR QUE OS PERDIDOS SE PERDEM")
    A("-" * 78)
    if not falhas:
        A("  Nenhum arquivo perdido. É um resultado raro; confira o escopo da pasta.")
    for status in STATUS_DE_FALHA:
        qtd = por_status.get(status, 0)
        if not qtd:
            continue
        A(_linha_pct(status, qtd, base))
        A(f"      -> {ACAO_POR_STATUS[status]}")
    paginas_para_ocr = sum(r.paginas or 0 for r in resultados if r.status == PDF_ESCANEADO)
    if paginas_para_ocr:
        A(f"  Total de páginas a passar por OCR: {paginas_para_ocr}")

    A("")
    A("4. QUALIDADE DO QUE PASSA")
    A("-" * 78)
    caracteres = [r.caracteres for r in aproveitaveis]
    if caracteres:
        p = _percentis(caracteres)
        A(
            f"  Caracteres por arquivo: min {p['min']} | p10 {p['p10']} | "
            f"mediana {p['mediana']} | p90 {p['p90']} | max {p['max']}"
        )
        faixas = [
            ("até 200 caracteres (chunk raso)", lambda c: c < 200),
            ("200 a 1.000", lambda c: 200 <= c < 1000),
            ("1.000 a 5.000", lambda c: 1000 <= c < 5000),
            ("acima de 5.000", lambda c: c >= 5000),
        ]
        for rotulo, teste in faixas:
            A(_linha_pct(rotulo, sum(1 for c in caracteres if teste(c)), len(caracteres)))
        A(_linha_pct("Rendem 1 único trecho", sum(1 for r in aproveitaveis if r.chunks == 1), len(aproveitaveis)))
        A(
            "  Nota: arquivo com pouco texto passa pelo filtro de 40 caracteres e vira"
        )
        A(
            "  um chunk que nunca será um bom resultado de busca — perda silenciosa"
        )
        A("  diferente da do item 3, e invisível em qualquer contagem de arquivos.")
    else:
        A("  Nenhum arquivo aproveitável para medir.")

    cegos, servidos = produtos_sem_documento(resultados)
    A("")
    A("5. PRODUTOS SEM NENHUM DOCUMENTO APROVEITÁVEL")
    A("-" * 78)
    A(f"  Produtos identificados na árvore:        {len(cegos) + len(servidos)}")
    A(f"  Com pelo menos 1 documento aproveitável: {len(servidos)}")
    A(_linha_pct("CEGOS (o agente não acha, jamais)", len(cegos), len(cegos) + len(servidos)))
    por_duplicata = cegos_por_duplicata(resultados)
    if por_duplicata:
        A(
            f"  Destes, {len(por_duplicata)} só têm documento idêntico a outro já contado:"
        )
        A("    o texto está indexado sob o arquivo vencedor, de outra pasta. Não é")
        A("    caso de OCR — é documento repetido no acervo.")
    for nome in cegos[:25]:
        marca = "  (só duplicatas)" if nome in set(por_duplicata) else ""
        A(f"      - {nome}{marca}")
    if len(cegos) > 25:
        A(f"      ... e mais {len(cegos) - 25} (lista completa no CSV)")

    A("")
    A("=" * 78)
    if base:
        pct_perdido = 100 * len(falhas) / base
        pct_escaneado = 100 * por_status.get(PDF_ESCANEADO, 0) / base
        A(f"RESUMO: {pct_perdido:.1f}% dos candidatos não rendem nada; "
          f"{pct_escaneado:.1f}% são escaneados (OCR resolveria).")
        if pct_escaneado >= 15:
            A("LEITURA: OCR na ingestão passa à frente de qualquer ajuste de recuperação.")
        elif pct_perdido >= 10:
            A("LEITURA: a perda é relevante, mas dividida — ver o item 3 antes de escolher a ação.")
        else:
            A("LEITURA: a extração não é o gargalo principal; a recuperação é.")
    A("=" * 78)
    return "\n".join(L)


CABECALHO_CSV = [
    "caminho", "produto", "extensao", "status", "acao_sugerida",
    "caracteres", "chunks", "paginas", "tamanho_bytes", "detalhe",
]


def escrever_csv(caminho_csv: str, resultados: Sequence[ResultadoArquivo], todos: bool = False) -> int:
    """CSV com ';' e BOM: é o que o Excel em português abre em colunas sem
    ninguém ter que passar pelo assistente de importação. A Qualidade é quem
    vai agir arquivo a arquivo a partir daqui."""
    selecionados = [
        r for r in resultados
        if todos or r.status in STATUS_DE_FALHA or r.status == OK_POBRE
    ]
    pasta = os.path.dirname(os.path.abspath(caminho_csv))
    if pasta:
        os.makedirs(pasta, exist_ok=True)
    with open(caminho_csv, "w", encoding="utf-8-sig", newline="") as f:
        escritor = csv.writer(f, delimiter=";")
        escritor.writerow(CABECALHO_CSV)
        for r in selecionados:
            escritor.writerow([
                r.caminho, r.produto or "", _extensao(r.caminho), r.status,
                ACAO_POR_STATUS.get(r.status, ""), r.caracteres, r.chunks,
                "" if r.paginas is None else r.paginas, r.tamanho_bytes, r.detalhe,
            ])
    return len(selecionados)


# --- CLI ------------------------------------------------------------------


def montar_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="diagnostico_extracao.py",
        description=(
            "Mede quanto do acervo a ingestão perde em silêncio. Somente leitura: "
            "não indexa, não toca no Qdrant, não escreve nada no acervo."
        ),
    )
    p.add_argument("caminho", nargs="?", default=ACERVO_PADRAO,
                   help=f"pasta do acervo (padrão: {ACERVO_PADRAO})")
    p.add_argument("--limite", type=int, default=None, metavar="N",
                   help="amostra de N arquivos espaçados pela árvore (prova rápida antes do acervo inteiro)")
    p.add_argument("--csv", default=None, metavar="ARQUIVO",
                   help="grava a lista classificada dos arquivos problemáticos")
    p.add_argument("--csv-todos", action="store_true",
                   help="no CSV, inclui também os arquivos que passaram")
    p.add_argument("--progresso", type=int, default=50, metavar="N",
                   help="mostra andamento a cada N arquivos (padrão: 50)")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = montar_parser().parse_args(argv)

    # pypdf despeja um aviso por PDF malformado. Num acervo cheio deles, o
    # relatório fica ilegível — e o aviso já virou linha classificada no CSV.
    logging.getLogger("pypdf").setLevel(logging.ERROR)

    raiz = args.caminho
    if not os.path.isdir(raiz):
        print(f"❌ Pasta inacessível: {raiz}")
        print("   No servidor, confira se o compartilhamento de rede está montado.")
        return 2

    print(f"🔎 Varrendo {raiz} ... (somente leitura)")
    todos = listar_arquivos(raiz)
    a_varrer, descartados_formato, nao_suportados = selecionar_arquivos(todos)
    selecionados = amostrar(a_varrer, args.limite)

    print(
        f"   {len(todos)} arquivo(s) na árvore | {len(a_varrer)} candidato(s) após dedup | "
        f"varrendo {len(selecionados)}"
    )

    v = Varredura(
        total_na_arvore=len(todos),
        extensoes=Counter(_extensao(f) for f in todos),
        duplicatas_formato=len(descartados_formato),
        nao_suportados=len(nao_suportados),
        amostrada=len(selecionados) < len(a_varrer),
    )

    inicio = time.monotonic()
    try:
        v.resultados = varrer(selecionados, args.progresso, _progresso_no_terminal)
    except KeyboardInterrupt:
        # Varredura de horas interrompida no meio ainda responde a pergunta:
        # relatório parcial vale muito mais que traceback e nenhum número.
        v.interrompida = True
        print("\n⏹️  Interrompido — relatório parcial do que já foi varrido.")
    v.segundos = time.monotonic() - inicio

    print()
    print(montar_relatorio(v, raiz))

    if args.csv:
        quantidade = escrever_csv(args.csv, v.resultados, todos=args.csv_todos)
        print(f"\n📄 CSV com {quantidade} linha(s): {os.path.abspath(args.csv)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
