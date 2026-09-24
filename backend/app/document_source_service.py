"""Referencias baixaveis para arquivos usados como fonte pelo RAG.

Interface do modulo:

- `montar_source_refs(docs)`: recebe payloads recuperados do Qdrant e devolve
  referencias seguras para a resposta do agente.
- `resolver_source_id(source_id)`: recebe o id opaco usado na URL de download e
  encontra o arquivo correspondente dentro das raizes permitidas.

O cliente nunca recebe `filepath`. O id e um HMAC do caminho canonico; para
resolver, varremos as raizes permitidas e comparamos o mesmo HMAC. Isso mantem a
referencia estavel entre conversas sem criar uma tabela nova so para mapear
arquivo -> token.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import hmac
import os
from pathlib import Path
from typing import Any, Iterable

from app.config import RAG_DOWNLOAD_PATH_ALIASES, RAG_DOWNLOAD_ROOTS, SECRET_KEY


DOWNLOAD_ROUTE_PREFIX = "/api/documentos/fontes"
_SOURCE_ID_PREFIX = "src_"
_SOURCE_ID_HEX_LENGTH = 32
_SOURCE_ID_CACHE: dict[str, Path] = {}


class SourceIdInvalidoError(ValueError):
    """O identificador nao tem o formato emitido por este modulo."""


class SourceNaoEncontradaError(FileNotFoundError):
    """A referencia era valida em forma, mas nenhum arquivo permitido existe."""


@dataclass(frozen=True)
class SourceRef:
    id: str
    nome_arquivo: str
    download_url: str


def _normalizar_caminho(path: Path) -> str:
    """Forma estavel para assinatura e comparacao.

    `normcase` so altera algo em sistemas case-insensitive, como Windows. Em
    Linux, preserva o caminho. `resolve(strict=False)` remove `..` mesmo quando
    o arquivo nao existe mais.
    """
    return os.path.normcase(str(path.resolve(strict=False)))


def _source_id_para_caminho(path: Path) -> str:
    assinatura = hmac.new(
        SECRET_KEY.encode("utf-8"),
        _normalizar_caminho(path).encode("utf-8"),
        sha256,
    ).hexdigest()[:_SOURCE_ID_HEX_LENGTH]
    return f"{_SOURCE_ID_PREFIX}{assinatura}"


def _raizes_permitidas() -> list[Path]:
    return [Path(raiz).resolve(strict=False) for raiz in RAG_DOWNLOAD_ROOTS]


def _esta_em_raiz_permitida(path: Path, raizes: Iterable[Path]) -> bool:
    try:
        caminho = path.resolve(strict=False)
    except OSError:
        return False
    for raiz in raizes:
        try:
            caminho.relative_to(raiz)
            return True
        except ValueError:
            continue
    return False


def _partes_de_caminho_textual(caminho: str) -> list[str]:
    return [parte for parte in caminho.replace("\\", "/").split("/") if parte]


def _caminho_por_alias(filepath: str, raizes: Iterable[Path]) -> Path | None:
    partes_filepath = _partes_de_caminho_textual(filepath)
    partes_filepath_norm = [parte.casefold() for parte in partes_filepath]

    for origem, destino in RAG_DOWNLOAD_PATH_ALIASES:
        partes_origem = _partes_de_caminho_textual(origem)
        partes_origem_norm = [parte.casefold() for parte in partes_origem]
        if not partes_origem_norm:
            continue
        if partes_filepath_norm[: len(partes_origem_norm)] != partes_origem_norm:
            continue

        candidato = Path(destino)
        for parte in partes_filepath[len(partes_origem):]:
            candidato = candidato / parte
        if _esta_em_raiz_permitida(candidato, raizes):
            return candidato
    return None


def _buscar_por_filename(filename: str, raizes: Iterable[Path]) -> Path | None:
    nome_normalizado = filename.casefold()
    candidatos: list[Path] = []
    for raiz in raizes:
        if not raiz.exists() or not raiz.is_dir():
            continue
        for candidato in raiz.rglob("*"):
            if candidato.is_file() and candidato.name.casefold() == nome_normalizado:
                candidatos.append(candidato)
    if not candidatos:
        return None
    return sorted(candidatos, key=_normalizar_caminho)[0]


def _caminho_baixavel_do_doc(
    doc: dict[str, Any],
    raizes: list[Path],
    *,
    buscar_por_filename: bool = True,
) -> Path | None:
    filepath = doc.get("filepath")
    if filepath:
        caminho_alias = _caminho_por_alias(str(filepath), raizes)
        if caminho_alias is not None:
            return caminho_alias

        caminho = Path(str(filepath))
        if _esta_em_raiz_permitida(caminho, raizes):
            return caminho

    filename = doc.get("filename")
    if not filename or not buscar_por_filename:
        return None
    return _buscar_por_filename(str(filename), raizes)


def montar_source_refs(
    docs: list[dict[str, Any]], *, buscar_por_filename: bool = True
) -> list[dict[str, str]]:
    """Monta referencias baixaveis a partir dos payloads recuperados do Qdrant.

    Deduplica por caminho canonico, ignora payload incompleto e ignora arquivos
    fora das raizes permitidas. Quando o payload traz um `filepath` antigo ou
    de outro ambiente, tenta resolver pelo `filename` dentro das raizes
    configuradas. A interface devolve dicts para encaixar direto nos payloads
    JSON ja usados pelos endpoints. Em consultas de catalogo,
    `buscar_por_filename` pode ser desativado para impedir uma varredura
    recursiva de SMB quando o payload nao traz um caminho resolvivel por alias.
    """
    raizes = _raizes_permitidas()
    vistos: set[str] = set()
    refs: list[SourceRef] = []

    for doc in docs:
        caminho = _caminho_baixavel_do_doc(
            doc, raizes, buscar_por_filename=buscar_por_filename
        )
        if caminho is None:
            continue

        chave = _normalizar_caminho(caminho)
        if chave in vistos:
            continue
        vistos.add(chave)

        nome = str(doc.get("filename") or caminho.name)
        source_id = _source_id_para_caminho(caminho)
        _SOURCE_ID_CACHE[source_id] = caminho
        refs.append(
            SourceRef(
                id=source_id,
                nome_arquivo=nome,
                download_url=f"{DOWNLOAD_ROUTE_PREFIX}/{source_id}/download",
            )
        )

    return [asdict(ref) for ref in refs]


def _validar_source_id(source_id: str) -> None:
    if not isinstance(source_id, str):
        raise SourceIdInvalidoError("Identificador de fonte invalido.")
    if not source_id.startswith(_SOURCE_ID_PREFIX):
        raise SourceIdInvalidoError("Identificador de fonte invalido.")
    digest = source_id[len(_SOURCE_ID_PREFIX):]
    if len(digest) != _SOURCE_ID_HEX_LENGTH:
        raise SourceIdInvalidoError("Identificador de fonte invalido.")
    if any(c not in "0123456789abcdef" for c in digest):
        raise SourceIdInvalidoError("Identificador de fonte invalido.")


def resolver_source_id(source_id: str) -> Path:
    """Resolve um id opaco para arquivo existente dentro das raizes permitidas."""
    _validar_source_id(source_id)
    raizes = _raizes_permitidas()
    caminho_em_cache = _SOURCE_ID_CACHE.get(source_id)
    if (
        caminho_em_cache is not None
        and caminho_em_cache.is_file()
        and _esta_em_raiz_permitida(caminho_em_cache, raizes)
        and _source_id_para_caminho(caminho_em_cache) == source_id
    ):
        return caminho_em_cache

    for raiz in raizes:
        if not raiz.exists() or not raiz.is_dir():
            continue
        for candidato in raiz.rglob("*"):
            if not candidato.is_file():
                continue
            if _source_id_para_caminho(candidato) == source_id:
                return candidato
    raise SourceNaoEncontradaError("Arquivo de fonte nao encontrado.")
