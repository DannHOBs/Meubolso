"""Confere se o app.py e os templates combinam entre si.

Roda no GitHub antes de gerar o APK. Se algum arquivo estiver faltando ou for de
outra versão (por exemplo, templates novos com um app.py antigo), o build para
com uma mensagem clara, em vez de gerar um APK que dá "Internal Server Error".

Uso (na raiz do projeto):  python tests/conferir_arquivos.py
"""

import pathlib
import re
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
erros = []

app_py = (RAIZ / "app.py").read_text(encoding="utf-8")
pasta_templates = RAIZ / "templates"

if not pasta_templates.is_dir():
    print("ERRO: a pasta 'templates' não existe na raiz do projeto.")
    sys.exit(1)

templates_existentes = {p.name for p in pasta_templates.glob("*.html")}

# 1) nomes de funções (endpoints) definidos no app.py
endpoints = set(re.findall(r"^def (\w+)\(", app_py, re.M)) | {"static"}

# 2) templates pedidos pelo app.py
pedidos = set(re.findall(r'render_template\(\s*"([^"]+\.html)"', app_py))
pedidos |= set(re.findall(r'montar_painel\(\s*"([^"]+\.html)"', app_py))

for nome in sorted(pedidos):
    if nome not in templates_existentes:
        erros.append(f"app.py usa o template '{nome}', mas ele não está na pasta templates/")

# 3) templates pedidos por outros templates (extends / include)
for arquivo in sorted(pasta_templates.glob("*.html")):
    texto = arquivo.read_text(encoding="utf-8")

    for alvo in re.findall(r'\{%\s*(?:extends|include)\s+"([^"]+)"', texto):
        if alvo not in templates_existentes:
            erros.append(f"{arquivo.name} usa '{alvo}', que não está na pasta templates/")

    # 4) url_for('x') nos templates precisa existir no app.py
    for endpoint in re.findall(r"url_for\(\s*['\"](\w+)['\"]", texto):
        if endpoint not in endpoints:
            erros.append(f"{arquivo.name} usa url_for('{endpoint}'), mas o app.py não tem essa rota")

if erros:
    print("ARQUIVOS INCOMPATÍVEIS ENTRE SI:")
    for e in sorted(set(erros)):
        print("  -", e)
    print("\nConfira se app.py e a pasta templates foram enviados juntos, da mesma versão.")
    sys.exit(1)

print(f"OK: {len(templates_existentes)} templates e {len(endpoints) - 1} funções conferidos, sem incompatibilidades.")
