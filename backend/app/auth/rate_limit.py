"""
Rate limiting do login (Fase 5, ticket 10 do plano de correção — AUD-010).

Em memória, por processo — sem Redis porque hoje só existe 1 container
`backend` (ver docker-compose.yml). Se o backend algum dia escalar pra mais
de uma réplica, isso deixa de proteger de verdade (cada réplica teria seu
próprio contador) — débito documentado, não resolvido aqui: exigiria um
armazenamento compartilhado (Redis é o candidato óbvio).

Conta por USERNAME, não por conta real — um username que não existe também
entra no limite. Sem isso, o próprio bloqueio virava mais um canal de
enumeração (só quem tem conta real seria bloqueado), a mesma categoria de
bug do canal lateral de tempo corrigido no ticket 3.
"""
import threading
import time
from collections import defaultdict, deque

MAX_TENTATIVAS = 5
JANELA_SEGUNDOS = 60

_tentativas: dict[str, deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def _purgar_antigas(fila: "deque[float]", agora: float) -> None:
    while fila and agora - fila[0] > JANELA_SEGUNDOS:
        fila.popleft()


def limite_excedido(username: str) -> bool:
    with _lock:
        agora = time.monotonic()
        fila = _tentativas[username]
        _purgar_antigas(fila, agora)
        return len(fila) >= MAX_TENTATIVAS


def registrar_tentativa_falha(username: str) -> None:
    with _lock:
        agora = time.monotonic()
        fila = _tentativas[username]
        _purgar_antigas(fila, agora)
        fila.append(agora)


def limpar_tentativas(username: str) -> None:
    with _lock:
        _tentativas.pop(username, None)
