# -*- coding: utf-8 -*-
"""Gera os assets de marca do PU Matcher a partir dos originais do Grupo Flexível.

Os originais vêm em altíssima resolução (ícone 4191x4500, logo 8913x2502) — são
arquivos de identidade visual, não assets de web. Servir isso direto custaria
~800 KB por carregamento de tela e travaria a instalação do PWA, que exige
ícones em tamanhos específicos.

Rodar quando os originais mudarem:

    python frontend/static/gerar_assets_marca.py

Por que os derivados ficam versionados no repositório, e não gerados no build:
o container do frontend não tem Pillow (não está em requirements.txt), e
adicionar uma dependência de imagem só para isso seria pior que versionar 5
PNGs pequenos.
"""
import os
from PIL import Image

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATIC = os.path.join(RAIZ, "frontend", "static")
ORIGINAIS = os.path.join(STATIC, "brand")

ICONE = os.path.join(ORIGINAIS, "grupo-flexivel-icone.png")
LOGO = os.path.join(ORIGINAIS, "grupo-flexivel-logo.png")


def _quadrado(imagem: Image.Image, margem: float = 0.0) -> Image.Image:
    """Centraliza numa tela quadrada transparente.

    O ícone original é 4191x4500 — quase quadrado, mas não exatamente. Um
    resize direto para 512x512 distorceria a marca; e ícone de PWA precisa ser
    quadrado, senão o Android recorta torto. `margem` reserva uma borda: o
    ícone maskable pode ser cortado em círculo pelo sistema, então o desenho
    precisa caber na "zona segura" central."""
    largura, altura = imagem.size
    lado = int(max(largura, altura) * (1 + margem * 2))
    tela = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    tela.paste(imagem, ((lado - largura) // 2, (lado - altura) // 2), imagem)
    return tela


def gerar() -> list:
    icone = Image.open(ICONE).convert("RGBA")
    logo = Image.open(LOGO).convert("RGBA")
    gerados = []

    # Ícones quadrados do PWA. 192 e 512 são os dois tamanhos que o Chrome
    # exige para considerar o app instalável.
    quadrado = _quadrado(icone)
    for tamanho in (192, 512):
        destino = os.path.join(STATIC, f"icon-{tamanho}.png")
        quadrado.resize((tamanho, tamanho), Image.LANCZOS).save(destino, optimize=True)
        gerados.append(destino)

    # Versão maskable: mesma arte com 20% de folga, para sobreviver ao recorte
    # circular do Android sem perder pedaço do símbolo.
    destino = os.path.join(STATIC, "icon-maskable-512.png")
    _quadrado(icone, margem=0.2).resize((512, 512), Image.LANCZOS).save(destino, optimize=True)
    gerados.append(destino)

    # Favicon da aba do navegador.
    destino = os.path.join(STATIC, "favicon.png")
    quadrado.resize((64, 64), Image.LANCZOS).save(destino, optimize=True)
    gerados.append(destino)

    # Logo horizontal (com o nome), para o topo da tela de login. Altura fixa
    # preservando a proporção — a largura é o que varia entre versões da marca.
    altura_alvo = 120
    largura = round(logo.width * altura_alvo / logo.height)
    destino = os.path.join(STATIC, "logo.png")
    logo.resize((largura, altura_alvo), Image.LANCZOS).save(destino, optimize=True)
    gerados.append(destino)

    return gerados


if __name__ == "__main__":
    for caminho in gerar():
        tamanho_kb = os.path.getsize(caminho) / 1024
        with Image.open(caminho) as im:
            print(f"{os.path.basename(caminho):<24} {im.size[0]}x{im.size[1]:<6} {tamanho_kb:6.1f} KB")
