"""
Validação técnica das respostas do agente — medição, não memória.

O CICLO QUE ESTE MÓDULO FECHA

1. As pessoas perguntam. Cada resposta já fica gravada em
   `conversation_messages`, agora com o caminho do motor que a produziu e os
   termos que a busca procurou.
2. Um técnico (Qualidade / Engenharia de Aplicação) abre a fila, lê perguntas
   REAIS e marca correta / incorreta / incompleta.
3. O relatório diz a taxa de acerto e — o que muda a fila de trabalho — a taxa
   POR CAMINHO do motor, mais a lista das perguntas que erraram.

NINGUÉM INVENTA PERGUNTA. O conjunto de avaliação é o histórico de uso. Se o
histórico for pequeno, o relatório diz isso em vez de dar um número bonito em
cima de quatro casos — ver `agregar_relatorio` e os campos `amostra_suficiente`
e `avisos`.

O QUE ESTE MÓDULO NÃO FAZ, DE PROPÓSITO

Nada do que ele grava volta para o prompt do agente. O projeto já teve um
incidente com feedback bruto sendo injetado no contexto (achado 2 de
docs/avaliacao_agente_2026-09-10.md). Veredito é dado de medição; o caminho
para ensinar o agente continua sendo `ItemTreinamento`, que tem aprovação
própria e é um ato deliberado de alguém.
"""
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.models import (
    ConversationMessage,
    Feedback,
    User,
    Veredito,
    VereditoTecnico,
)
from app.rag.caminhos import CAMINHO_DESCONHECIDO, rotulo_de_caminho


# Abaixo disto, uma taxa por caminho é ruído: com três respostas avaliadas,
# um erro muda a taxa em 33 pontos. O número não bloqueia nada — só marca a
# linha como insuficiente, para ninguém reorganizar a fila de trabalho em
# cima de uma amostra que não sustenta a conclusão.
MINIMO_POR_CAMINHO = 5
# O mesmo para a taxa geral.
MINIMO_GERAL = 20


class MensagemNaoAvaliavelError(Exception):
    """Alvo inexistente, ou que não é uma resposta do agente."""


class VeredictoInvalidoError(Exception):
    pass


# ---------------------------------------------------------------------------
# Leitura: a fila do técnico
# ---------------------------------------------------------------------------

def _pergunta_que_originou(session: Session, mensagem: ConversationMessage) -> str:
    """A mensagem de usuário imediatamente anterior à resposta.

    A pergunta não está na linha da resposta — está na mensagem anterior da
    mesma conversa. Ordena por (created_at, id) porque as duas são gravadas no
    mesmo instante em `save_exchange`: sem o desempate por id, qual vem
    primeiro seria indeterminado.
    """
    anterior = (
        session.query(ConversationMessage)
        .filter(
            ConversationMessage.conversation_id == mensagem.conversation_id,
            ConversationMessage.role == "user",
            sa.tuple_(ConversationMessage.created_at, ConversationMessage.id)
            < sa.tuple_(mensagem.created_at, mensagem.id),
        )
        .order_by(
            ConversationMessage.created_at.desc(), ConversationMessage.id.desc()
        )
        .first()
    )
    return anterior.content if anterior is not None else ""


def listar_fila(
    session: Session,
    *,
    apenas_sem_veredito: bool = True,
    caminho: Optional[str] = None,
    apenas_com_feedback_negativo: bool = False,
    limite: int = 50,
) -> List[Dict[str, Any]]:
    """Respostas reais do agente aguardando julgamento, da mais recente à mais antiga.

    `apenas_com_feedback_negativo` cruza com a tabela `feedback`, que já existe
    e guarda o útil/não útil de quem perguntou. Não é o mesmo julgamento — o
    vendedor é leigo —, mas é o melhor sinal barato de por onde começar quando
    o histórico é grande e o tempo do técnico é curto.
    """
    consulta = session.query(ConversationMessage).filter(
        ConversationMessage.role == "assistant"
    )

    if caminho:
        if caminho == CAMINHO_DESCONHECIDO:
            consulta = consulta.filter(ConversationMessage.caminho.is_(None))
        else:
            consulta = consulta.filter(ConversationMessage.caminho == caminho)

    if apenas_sem_veredito:
        ja_avaliadas = session.query(VereditoTecnico.mensagem_id).filter(
            VereditoTecnico.mensagem_id.isnot(None)
        )
        consulta = consulta.filter(ConversationMessage.id.notin_(ja_avaliadas))

    if apenas_com_feedback_negativo:
        negativas = session.query(Feedback.answer).filter(Feedback.util.is_(False))
        consulta = consulta.filter(ConversationMessage.content.in_(negativas))

    mensagens = (
        consulta.order_by(ConversationMessage.created_at.desc())
        .limit(max(1, min(limite, 200)))
        .all()
    )

    return [
        {
            "mensagem_id": str(m.id),
            "pergunta": _pergunta_que_originou(session, m),
            "resposta": m.content,
            "caminho": m.caminho or CAMINHO_DESCONHECIDO,
            "caminho_rotulo": rotulo_de_caminho(m.caminho),
            "model_used": m.model_used,
            "fontes": list(m.sources or []),
            "termos_busca": list(m.termos_busca or []),
            "created_at": m.created_at,
        }
        for m in mensagens
    ]


# ---------------------------------------------------------------------------
# Escrita: o veredito
# ---------------------------------------------------------------------------

def registrar_veredito(
    session: Session,
    *,
    mensagem_id: uuid.UUID,
    avaliador: User,
    veredito: Veredito,
    resposta_correta: Optional[str] = None,
    justificativa: Optional[str] = None,
) -> VereditoTecnico:
    """Julga uma resposta já dada. Reavaliar pelo mesmo técnico atualiza a linha.

    Copia pergunta, resposta, caminho, modelo e fontes para a linha do
    veredito: a conversa é do vendedor e ele pode apagá-la, e o conjunto de
    regressão não pode encolher por causa disso (ver o model).
    """
    if not isinstance(veredito, Veredito):
        raise VeredictoInvalidoError(f"Veredito desconhecido: {veredito!r}")

    mensagem = session.get(ConversationMessage, mensagem_id)
    if mensagem is None:
        raise MensagemNaoAvaliavelError("Resposta não encontrada.")
    if mensagem.role != "assistant":
        # Julgar a pergunta do vendedor não significa nada — e deixar passar
        # contaminaria a taxa de acerto com linhas que não medem o motor.
        raise MensagemNaoAvaliavelError(
            "Só é possível validar uma resposta do agente, não uma pergunta."
        )

    registro = (
        session.query(VereditoTecnico)
        .filter(
            VereditoTecnico.mensagem_id == mensagem_id,
            VereditoTecnico.avaliado_por_id == avaliador.id,
        )
        .first()
    )
    if registro is None:
        registro = VereditoTecnico(
            mensagem_id=mensagem.id, avaliado_por_id=avaliador.id
        )
        session.add(registro)

    registro.pergunta = _pergunta_que_originou(session, mensagem)
    registro.resposta = mensagem.content
    registro.caminho = mensagem.caminho
    registro.model_used = mensagem.model_used
    registro.fontes = list(mensagem.sources or [])
    registro.termos_busca = list(mensagem.termos_busca or [])
    registro.veredito = veredito
    registro.resposta_correta = (resposta_correta or "").strip() or None
    registro.justificativa = (justificativa or "").strip() or None

    session.commit()
    session.refresh(registro)
    return registro


# ---------------------------------------------------------------------------
# Relatório
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LinhaDeVeredito:
    """Um veredito reduzido ao que o relatório precisa.

    A agregação trabalha sobre isto, e não sobre o model do SQLAlchemy, para
    ser testável sem banco — a suíte deste projeto roda sem PostgreSQL, e uma
    regra de contagem que só pode ser verificada com banco de pé é uma regra
    que ninguém verifica.
    """
    veredito: str
    caminho: Optional[str]
    pergunta: str
    mensagem_id: Optional[str] = None
    resposta_correta: Optional[str] = None
    justificativa: Optional[str] = None
    model_used: Optional[str] = None
    avaliado_por: Optional[str] = None
    created_at: Optional[datetime] = None


DIVERGENTE = "divergente"


def _chave_da_resposta(linha: LinhaDeVeredito, indice: int) -> str:
    """Uma resposta avaliada por dois técnicos é UMA resposta, não duas.

    Sem isto, uma resposta revisada em dupla pesaria o dobro na taxa. Veredito
    órfão (conversa apagada, `mensagem_id` nulo) não tem com quem agrupar e
    vira chave própria.
    """
    return linha.mensagem_id or f"__orfao__{indice}"


def agregar_relatorio(
    linhas: Iterable[LinhaDeVeredito],
    *,
    total_respostas_no_periodo: int = 0,
    minimo_por_caminho: int = MINIMO_POR_CAMINHO,
    minimo_geral: int = MINIMO_GERAL,
) -> Dict[str, Any]:
    """Taxa geral, taxa por caminho e lista de regressão. Função pura.

    REGRA DE DIVERGÊNCIA: quando dois técnicos julgam a mesma resposta de
    formas diferentes, a resposta não entra na taxa de acerto — entra numa
    contagem própria e na lista de regressão. Escolher um dos dois vereditos
    (o mais recente, o mais severo) seria inventar um consenso que não houve;
    divergência entre especialistas normalmente significa que a pergunta era
    ambígua, e isso é informação sobre o acervo, não sobre o motor.
    """
    linhas = list(linhas)

    por_resposta: Dict[str, Dict[str, Any]] = {}
    for indice, linha in enumerate(linhas):
        chave = _chave_da_resposta(linha, indice)
        grupo = por_resposta.setdefault(
            chave,
            {
                "vereditos": set(),
                "caminho": linha.caminho or CAMINHO_DESCONHECIDO,
                "pergunta": linha.pergunta,
                "mensagem_id": linha.mensagem_id,
                "model_used": linha.model_used,
                "correcoes": [],
            },
        )
        grupo["vereditos"].add(linha.veredito)
        if linha.resposta_correta or linha.justificativa:
            grupo["correcoes"].append(
                {
                    "avaliado_por": linha.avaliado_por,
                    "resposta_correta": linha.resposta_correta,
                    "justificativa": linha.justificativa,
                }
            )

    contagem_por_caminho: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {
            "total": 0,
            Veredito.CORRETA.value: 0,
            Veredito.INCORRETA.value: 0,
            Veredito.INCOMPLETA.value: 0,
            DIVERGENTE: 0,
        }
    )
    regressao: List[Dict[str, Any]] = []

    for grupo in por_resposta.values():
        vereditos = grupo["vereditos"]
        resultado = next(iter(vereditos)) if len(vereditos) == 1 else DIVERGENTE
        caixa = contagem_por_caminho[grupo["caminho"]]
        caixa["total"] += 1
        caixa[resultado] = caixa.get(resultado, 0) + 1

        if resultado != Veredito.CORRETA.value:
            regressao.append(
                {
                    "mensagem_id": grupo["mensagem_id"],
                    "pergunta": grupo["pergunta"],
                    "caminho": grupo["caminho"],
                    "caminho_rotulo": rotulo_de_caminho(grupo["caminho"]),
                    "veredito": resultado,
                    "model_used": grupo["model_used"],
                    "correcoes": grupo["correcoes"],
                }
            )

    def _taxa(corretas: int, julgadas: int) -> Optional[float]:
        # Sem julgamento consensual não existe taxa. `None` é honesto; 0.0
        # seria lido como "erra tudo", que é uma afirmação que o dado não faz.
        return round(corretas / julgadas, 4) if julgadas else None

    por_caminho = []
    for caminho, caixa in sorted(contagem_por_caminho.items()):
        julgadas = caixa["total"] - caixa[DIVERGENTE]
        por_caminho.append(
            {
                "caminho": caminho,
                "rotulo": rotulo_de_caminho(caminho),
                "total": caixa["total"],
                "corretas": caixa[Veredito.CORRETA.value],
                "incorretas": caixa[Veredito.INCORRETA.value],
                "incompletas": caixa[Veredito.INCOMPLETA.value],
                "divergentes": caixa[DIVERGENTE],
                "taxa_acerto": _taxa(caixa[Veredito.CORRETA.value], julgadas),
                "amostra_suficiente": caixa["total"] >= minimo_por_caminho,
            }
        )
    # Do pior para o melhor: o relatório existe para ordenar trabalho, e o
    # primeiro item tem de ser o caminho que mais erra. Caminho sem taxa
    # (só divergências) vai para o fim.
    por_caminho.sort(
        key=lambda c: (c["taxa_acerto"] is None, c["taxa_acerto"], -c["total"])
    )

    total_avaliadas = len(por_resposta)
    total_divergentes = sum(c["divergentes"] for c in por_caminho)
    total_corretas = sum(c["corretas"] for c in por_caminho)
    julgadas = total_avaliadas - total_divergentes

    avisos: List[str] = []
    if total_respostas_no_periodo == 0 and total_avaliadas == 0:
        avisos.append(
            "Não há nenhuma resposta registrada no período. O ciclo de validação "
            "só começa a produzir número depois que as pessoas usarem o agente — "
            "não há como substituir isso por perguntas inventadas."
        )
    elif total_avaliadas == 0:
        avisos.append(
            f"Há {total_respostas_no_periodo} respostas no período e nenhuma foi "
            "validada tecnicamente ainda. Não existe taxa de acerto para relatar."
        )
    elif total_avaliadas < minimo_geral:
        avisos.append(
            f"Amostra pequena: {total_avaliadas} respostas validadas (mínimo "
            f"recomendado: {minimo_geral}). A taxa abaixo serve para acompanhar "
            "tendência, não para afirmar a qualidade do sistema."
        )
    insuficientes = [c["rotulo"] for c in por_caminho if not c["amostra_suficiente"]]
    if insuficientes:
        avisos.append(
            "Caminhos com amostra insuficiente (menos de "
            f"{minimo_por_caminho} respostas validadas): {', '.join(insuficientes)}."
        )
    if total_divergentes:
        avisos.append(
            f"{total_divergentes} resposta(s) com vereditos divergentes entre "
            "técnicos — fora da taxa de acerto e listadas na regressão."
        )

    cobertura = (
        round(total_avaliadas / total_respostas_no_periodo, 4)
        if total_respostas_no_periodo
        else None
    )

    return {
        "total_respostas_no_periodo": total_respostas_no_periodo,
        "total_respostas_validadas": total_avaliadas,
        "cobertura": cobertura,
        "total_vereditos": len(linhas),
        "corretas": total_corretas,
        "incorretas": sum(c["incorretas"] for c in por_caminho),
        "incompletas": sum(c["incompletas"] for c in por_caminho),
        "divergentes": total_divergentes,
        "taxa_acerto": _taxa(total_corretas, julgadas),
        "amostra_suficiente": total_avaliadas >= minimo_geral,
        "por_caminho": por_caminho,
        "regressao": regressao,
        "avisos": avisos,
    }


def _linhas_do_banco(registros: Sequence[VereditoTecnico]) -> List[LinhaDeVeredito]:
    return [
        LinhaDeVeredito(
            veredito=r.veredito.value,
            caminho=r.caminho,
            pergunta=r.pergunta,
            mensagem_id=str(r.mensagem_id) if r.mensagem_id else None,
            resposta_correta=r.resposta_correta,
            justificativa=r.justificativa,
            model_used=r.model_used,
            avaliado_por=r.avaliado_por.nome if r.avaliado_por else None,
            created_at=r.created_at,
        )
        for r in registros
    ]


def gerar_relatorio(
    session: Session, *, desde: Optional[datetime] = None
) -> Dict[str, Any]:
    """Lê os vereditos do banco e entrega o relatório agregado."""
    consulta = session.query(VereditoTecnico)
    respostas = session.query(ConversationMessage).filter(
        ConversationMessage.role == "assistant"
    )
    if desde is not None:
        consulta = consulta.filter(VereditoTecnico.created_at >= desde)
        respostas = respostas.filter(ConversationMessage.created_at >= desde)

    relatorio = agregar_relatorio(
        _linhas_do_banco(consulta.order_by(VereditoTecnico.created_at).all()),
        total_respostas_no_periodo=respostas.count(),
    )
    relatorio["desde"] = desde.isoformat() if desde else None
    return relatorio
