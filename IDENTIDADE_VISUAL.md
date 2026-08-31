# 🎨 Identidade Visual — FIDC Grupo Flexível

Guia de identidade visual do **FIDC Grupo Flexível**, adaptado a partir
do guia de identidade visual do projeto **TV Corporativa** (Grupo Flexível).
A paleta é a mesma: cores extraídas do **logo oficial** (verdes da marca),
mantendo o acento laranja para alertas.

> Documento adaptado para este projeto (Next.js + Tailwind CSS). As mesmas
> variáveis de cor e a mesma fonte (Roboto) são usadas aqui, só a forma de
> aplicar muda: em vez de CSS solto em `login.html`/`admin.html`, as cores
> ficam centralizadas em `tailwind.config.ts` e em `app/globals.css`.

---

## 🟢 Paleta de cores

### Cores da marca (extraídas do logo)

| Função / Uso | Classe Tailwind | Hex | Onde aparece neste projeto |
|---|---|---|---|
| **Verde-petróleo** (primária escura) | `brand-petrol` | `#0C3B38` | Barra de navegação, títulos |
| **Teal** (primária de ação) | `brand-teal` | `#0F7C70` | Botões principais (Buscar, Cadastrar, Salvar, Entrar), links ativos |
| **Verde-limão** (acento da marca) | `brand-lime` | `#76C043` | Badges ("Cadastrado ✓"), realces |
| **Verde-petróleo médio** | `brand-teal-mid` | `#14534D` | Gradientes/hover em fundos escuros |

### Cores funcionais

| Função | Classe Tailwind | Hex | Uso |
|---|---|---|---|
| **Sucesso** | `brand-green` | `#4CAF50` | Mensagens de sucesso |
| **Alerta** | `brand-amber` | `#FFAB40` | Avisos (ex: token perto de expirar) |
| **Erro / Perigo** | `brand-red` | `#E06C75` | Mensagens de erro |
| **Cinza de fundo** | `brand-gray` | `#F0F4F8` | Fundo geral do app (`body`) |
| **Texto** | `brand-text` | `#2D3A4A` | Texto padrão |
| **Texto suave** | `brand-dim` | `#7A8FA6` | Legendas, textos secundários |

---

## 🎯 Onde as cores estão definidas

Diferente do projeto de origem (que usava `:root { --blue-dark: ... }` em CSS
puro), aqui a paleta vive em dois lugares que precisam ficar em sincronia:

**1. `tailwind.config.ts`** — fonte de verdade, usada nas classes utilitárias
(`bg-brand-teal`, `text-brand-petrol` etc.):

```ts
theme: {
  extend: {
    colors: {
      brand: {
        petrol: "#0C3B38",
        "teal-mid": "#14534D",
        teal: "#0F7C70",
        lime: "#76C043",
        amber: "#FFAB40",
        red: "#E06C75",
        green: "#4CAF50",
        gray: "#F0F4F8",
        text: "#2D3A4A",
        dim: "#7A8FA6",
      },
    },
  },
},
```

**2. `app/globals.css`** — as mesmas variáveis CSS, mantidas por
compatibilidade/documentação (não usadas diretamente pelos componentes, que
usam as classes Tailwind acima):

```css
:root {
  --brand-petrol:#0C3B38;
  --brand-teal-mid:#14534D;
  --brand-teal:#0F7C70;
  --brand-lime:#76C043;
  --brand-amber:#FFAB40;
  --brand-red:#E06C75;
  --brand-green:#4CAF50;
  --brand-gray:#F0F4F8;
  --brand-text:#2D3A4A;
  --brand-dim:#7A8FA6;
}
```

Se a marca mudar de cor no futuro, altere nos dois lugares (ou remova o bloco
CSS e centralize tudo no Tailwind).

---

## 🖼️ Logo

- **Arquivo:** `public/logo.png` — pasta padrão do Next.js para arquivos
  estáticos, servido automaticamente na raiz do site em `/logo.png`.
- **Como usar:** `<img src="/logo.png" alt="Grupo Flexível" />` (ou
  `next/image` apontando para `/logo.png`).
- **Fundo:** transparente — pode ser sobreposto a qualquer cor.
- **Regra de legibilidade:** o texto do logo é verde-escuro e **some em
  fundos escuros**. Por isso:
  - **Tela de login** (fundo claro, `bg-brand-gray`): logo exibido direto,
    sem selo.
  - **Barra de navegação** (fundo `brand-petrol`, escuro): logo dentro de um
    **selo branco arredondado** (`bg-white rounded-lg px-2 py-1`).
- **Não** distorcer, recolorir ou rotacionar o logo. Manter a proporção
  original (usar `w-auto` junto com uma altura fixa, nunca `w-full h-full`
  sem `object-contain`).

---

## 🎨 Aplicação por componente (neste projeto)

### Barra de navegação (`app/nav-bar.tsx`) — fundo escuro
```
bg-brand-petrol text-white
/* logo: */ <div className="bg-white rounded-lg px-2 py-1"><img src="/logo.png" .../></div>
/* link ativo: */ bg-brand-teal text-white
/* link inativo: */ text-white/80 hover:bg-white/10
```

### Botões principais (buscar, cadastrar, salvar, entrar)
```
bg-brand-teal hover:bg-brand-teal-mid text-white rounded-md
```

### Badges / status "Cadastrado ✓"
```
bg-brand-lime/20 text-brand-petrol
```

### Cards
```
bg-white rounded-lg shadow-sm border
```

### Mensagens
```
/* sucesso: */ bg-brand-green/10 text-brand-green border border-brand-green/30
/* erro: */    bg-brand-red/10 text-brand-red border border-brand-red/30
```

---

## 🔤 Tipografia

```css
font-family: 'Roboto', 'Segoe UI', Arial, sans-serif;
```

Roboto é carregada via Google Fonts em `app/layout.tsx` (`<link>` no
`<head>`), com fallback para **Segoe UI** caso a fonte não carregue.

### Pesos
- **300 (Light)** — subtítulos, descrições
- **400 (Regular)** — texto padrão
- **500 (Medium)** — labels, badges, botões
- **700 (Bold)** — títulos, nomes de cliente em destaque

---

## ✅ Checklist de consistência

- [x] Paleta principal derivada do logo oficial (verdes da marca)
- [x] Acento laranja (`brand-amber`) reservado para alertas
- [x] Logo em `public/logo.png` (não na raiz do projeto)
- [x] Logo com selo branco sobre a barra de navegação (fundo escuro)
- [x] Logo direto (sem selo) na tela de login (fundo claro)
- [x] Fonte Roboto com fallback Segoe UI
- [x] Cores centralizadas em `tailwind.config.ts` (+ espelhadas em `globals.css`)
- [x] Texto branco sobre verde-petróleo; texto escuro sobre fundo claro

---

## 🐍 Aplicação no PU Matcher (Streamlit) — 2026-08-26

Este guia foi escrito pro projeto FIDC (Next.js + Tailwind). O **PU Matcher**
(`c:\rag`) reaproveita a mesma paleta e tipografia, mas o mecanismo de
aplicação é outro — Streamlit não tem Tailwind nem controle de `<head>`:

| Token do guia | Onde vive aqui | Arquivo |
|---|---|---|
| `tailwind.config.ts` (cores de widgets prontos: botões, inputs) | `[theme]` nativo do Streamlit | `.streamlit/config.toml` |
| CSS solto de `globals.css` (tipografia Roboto, cards) | CSS injetado via `st.markdown(unsafe_allow_html=True)` | `frontend/app.py`, função `_inject_brand_css()` |
| `public/logo.png` | **Ainda não existe.** Nenhum logo real do Grupo Flexível foi fornecido — usa-se `frontend/static/icon.svg`, um monograma "PU" gerado na paleta (verde-petróleo `#0C3B38` + branco), só até o arquivo real ser fornecido. Troca é local: sobrescrever `frontend/static/icon.svg` (ou trocar por `.png` e ajustar as 2 referências: sidebar em `app.py` e `icons` do `manifest.json`) | `frontend/static/icon.svg` |
| Selo branco da navbar (fundo escuro) | Não aplicável — Streamlit não tem navbar customizável, só a sidebar (fundo claro por padrão do tema) | — |
| Cores funcionais (sucesso/alerta/erro) | Cobertas pelo tema nativo do Streamlit (`st.success`/`st.warning`/`st.error` já usam cores próximas; não sobrescritas para não conflitar com os componentes internos do framework) | `.streamlit/config.toml` |

Mapeamento de cor efetivo:

```toml
# .streamlit/config.toml
[theme]
primaryColor = "#0F7C70"            # brand-teal
backgroundColor = "#F0F4F8"          # brand-gray
secondaryBackgroundColor = "#FFFFFF"
textColor = "#2D3A4A"                # brand-text
```

Se a marca mudar de cor, os 4 valores acima + `frontend/app.py` (fonte
Roboto/estilo `.pu-card`) + `frontend/static/manifest.json`
(`theme_color`/`background_color`) precisam ficar em sincronia — mesmo aviso
do guia original, só que agora em 3 lugares em vez de 2.

---