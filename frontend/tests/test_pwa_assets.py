"""
Contrato do PWA (card de instalação, Sessão 27).

Não testa o comportamento do navegador (beforeinstallprompt, instalação de
fato) — isso exige um Chrome real, indisponível neste ambiente (mesma
limitação já registrada nas sessões de frontend anteriores). O que é
verificável sem browser: os arquivos estáticos que o navegador consome
existem, têm o conteúdo mínimo exigido pelos critérios de instalabilidade do
Chrome, e apontam uns para os outros de forma consistente.
"""
import json
import os

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def _read(filename):
    with open(os.path.join(STATIC_DIR, filename), encoding="utf-8") as f:
        return f.read()


def test_manifest_e_json_valido_com_campos_obrigatorios():
    manifest = json.loads(_read("manifest.json"))

    assert manifest["name"]
    assert manifest["short_name"]
    assert manifest["display"] == "standalone"
    # Precisam ser absolutos ("/"), não relativos — o manifest é servido em
    # /app/static/manifest.json, e "." relativo resolveria para essa pasta,
    # não para a raiz do app (ver docs/verificacao_auditoria_2026-08-26.md
    # sobre como esse tipo de erro de resolução de URL passa despercebido).
    assert manifest["start_url"] == "/"
    assert manifest["scope"] == "/"
    assert len(manifest["icons"]) >= 1


def test_icones_do_manifest_apontam_para_arquivo_que_existe():
    manifest = json.loads(_read("manifest.json"))

    for icon in manifest["icons"]:
        assert icon["src"].startswith("/app/static/")
        local_name = icon["src"].rsplit("/", 1)[-1]
        assert os.path.isfile(os.path.join(STATIC_DIR, local_name)), (
            f"manifest.json referencia {icon['src']}, mas frontend/static/{local_name} não existe"
        )


def test_service_worker_registra_handlers_exigidos_pelo_chrome():
    sw = _read("service-worker.js")

    # Critério de instalabilidade do Chrome: SW ativo com handler de "fetch".
    # Sem isso, beforeinstallprompt nunca dispara, independente de manifest.
    assert "addEventListener(\"fetch\"" in sw or "addEventListener('fetch'" in sw
    assert "addEventListener(\"install\"" in sw or "addEventListener('install'" in sw


def test_service_worker_nao_cacheia_o_stream_websocket_do_streamlit():
    # Guarda-corpo contra a armadilha mais fácil de PWA em cima de um app
    # com WebSocket vivo: a lista de assets cacheados (APP_SHELL) não pode
    # incluir a rota de stream do Streamlit, ou um cache-first quebraria a
    # sessão. Checa a lista de verdade, não o arquivo inteiro — o comentário
    # de design acima dela cita "_stcore" de propósito, isso não é o bug.
    sw = _read("service-worker.js")
    shell_start = sw.index("APP_SHELL")
    shell_line = sw[shell_start : sw.index("\n", shell_start)]
    assert "_stcore" not in shell_line


def test_streamlit_config_habilita_static_serving():
    config_path = os.path.join(
        os.path.dirname(STATIC_DIR), "..", ".streamlit", "config.toml"
    )
    with open(os.path.normpath(config_path), encoding="utf-8") as f:
        content = f.read()
    assert "enableStaticServing = true" in content


def test_caddyfile_serve_service_worker_na_raiz_com_escopo_permitido():
    caddyfile_path = os.path.join(
        os.path.dirname(STATIC_DIR), "..", "proxy", "Caddyfile"
    )
    with open(os.path.normpath(caddyfile_path), encoding="utf-8") as f:
        content = f.read()

    # O motivo de existir um proxy: o SW precisa estar em "/", não em
    # "/app/static/", ou o Chrome nunca considera o app instalável.
    assert "/service-worker.js" in content
    assert "reverse_proxy frontend:8501" in content


# --- assets de marca (Sessão 35d) ------------------------------------------
# Os PNGs são DERIVADOS dos originais de alta resolução em static/brand/, via
# `python frontend/static/gerar_assets_marca.py`. Ficam versionados porque a
# imagem do frontend não tem Pillow.

def test_pwa_tem_os_dois_tamanhos_de_icone_que_o_chrome_exige():
    """Sem um ícone de 192 E um de 512, o Chrome não considera o app
    instalável — e o card de instalação fica desabilitado para sempre, sem
    nenhum erro visível."""
    manifest = json.loads(_read("manifest.json"))
    tamanhos = {icon["sizes"] for icon in manifest["icons"]}
    assert "192x192" in tamanhos
    assert "512x512" in tamanhos


def test_manifest_declara_icone_maskable():
    """Sem `purpose: maskable`, o Android desenha o ícone dentro de um quadrado
    branco em vez de aplicar a máscara do sistema."""
    manifest = json.loads(_read("manifest.json"))
    assert any("maskable" in icon.get("purpose", "") for icon in manifest["icons"])


def test_icones_sao_quadrados_e_do_tamanho_declarado():
    """O original da marca é 4191x4500 — quase quadrado, mas não exatamente.
    Um resize direto distorceria a marca; por isso a arte é centralizada numa
    tela quadrada. Este teste é o que garante que o passo não foi pulado."""
    from PIL import Image

    manifest = json.loads(_read("manifest.json"))
    for icon in manifest["icons"]:
        nome = icon["src"].rsplit("/", 1)[-1]
        with Image.open(os.path.join(STATIC_DIR, nome)) as im:
            largura, altura = im.size
        assert largura == altura, f"{nome} não é quadrado: {largura}x{altura}"
        assert f"{largura}x{altura}" == icon["sizes"], (
            f"{nome} tem {largura}x{altura} mas o manifest declara {icon['sizes']}"
        )


def test_assets_de_marca_usados_pela_tela_existem():
    """`st.image` de caminho inexistente derruba a tela inteira, não degrada."""
    for nome in ("favicon.png", "logo.png", "icon-192.png"):
        assert os.path.isfile(os.path.join(STATIC_DIR, nome)), f"falta {nome}"


def test_originais_da_marca_ficam_versionados_para_regerar():
    """Sem os originais, refazer os derivados exigiria pedir os arquivos de
    volta ao time de marketing."""
    brand = os.path.join(STATIC_DIR, "brand")
    assert os.path.isfile(os.path.join(brand, "grupo-flexivel-icone.png"))
    assert os.path.isfile(os.path.join(brand, "grupo-flexivel-logo.png"))


def test_derivados_sao_leves_o_suficiente_para_web():
    """Os originais somam ~790 KB. Servir isso a cada carregamento de tela é o
    erro que este pipeline existe para evitar."""
    for nome in ("favicon.png", "logo.png", "icon-192.png", "icon-512.png"):
        kb = os.path.getsize(os.path.join(STATIC_DIR, nome)) / 1024
        assert kb < 100, f"{nome} tem {kb:.0f} KB — muito pesado para asset de tela"
