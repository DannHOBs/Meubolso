# Meu Bolso

App de finanças pessoais (nome de trabalho): receitas, despesas, categorias editáveis, resumo do mês e backup.
Mesma base do FINANCBETEL (Flask + SQLite + APK pelo GitHub), mas para uso pessoal, com um único usuário.

## O que já tem (versão 1)

- Primeiro acesso: nome + senha. Senha pedida a cada abertura; bloqueio de 60 s após 3 erros; sai sozinho após 15 min parado.
- Início: saldo do mês, entradas, saídas, saldo acumulado, gastos por categoria e últimos lançamentos.
- Lançamentos: adicionar receita/despesa (botão +), editar, excluir, filtrar por tipo, navegar entre meses.
- Categorias: criar, renomear, trocar o ícone, ocultar (os lançamentos antigos continuam).
- Backup: criar, baixar, restaurar a partir de um arquivo (para trocar de celular).
- Alterar senha.

## Testar no computador

    pip install -r requirements.txt
    python tests/teste_basico.py     # teste automático (usa banco temporário)
    python app.py                    # abre em http://127.0.0.1:5000

## Gerar o APK

1. Crie um repositório PRIVADO no GitHub e envie estes arquivos (mantenha a pasta `.github`).
2. Aba **Actions → Gerar APK → Run workflow**.
3. Baixe **MEUBOLSO-APK** em "Artifacts", descompacte e instale o `app-debug.apk`.

O app tem identificador próprio (`br.com.meubolso`): instala ao lado do FINANCBETEL, sem mexer nele.
Desinstalar o Meu Bolso apaga os dados: baixe um backup antes.
Python 3.13 está definido em `android/app/build.gradle` e no workflow; se mudar um, mude o outro.

## Ideias para as próximas versões

- Contas a pagar com vencimento e lembrete; despesas recorrentes.
- Metas / orçamento por categoria.
- Cartão de crédito e parcelas.
- Relatório em PDF.
