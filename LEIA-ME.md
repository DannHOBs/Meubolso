# Meu Bolso

App de finanças pessoais (nome de trabalho): receitas, despesas, categorias editáveis, resumo do mês e backup.
Mesma base do FINANCBETEL (Flask + SQLite + APK pelo GitHub), mas para uso pessoal, com um único usuário.

## O que tem

- Primeiro acesso: nome + senha. Senha pedida a cada abertura; bloqueio de 60 s após 3 erros; sai sozinho após 15 min parado.
- Início: saldo do mês, entradas, saídas, saldo acumulado, gastos por categoria e últimos lançamentos.
- Lançamentos: adicionar receita/despesa (botão +), editar, excluir, filtrar por tipo, navegar entre meses.
- Categorias: criar, renomear, trocar o ícone, ocultar (os lançamentos antigos continuam).
- Backup: criar, baixar, restaurar a partir de um arquivo (para trocar de celular).
- Alterar senha.

### Novo na versão 2: contas a pagar

- Aba **Contas**: contas com vencimento, separadas em "Atrasadas" e as do mês, com os totais "A pagar" e "Já pago".
- **✓ Pagar** registra a despesa nos Lançamentos (com a data de hoje) e marca a conta como paga.
  **Desfazer pagamento** remove esse lançamento e reabre a conta. Excluir o lançamento pela aba Lançamentos também reabre a conta.
- **Repetir todo mês**: marque ao criar a conta. As contas dos meses seguintes são criadas sozinhas (aluguel, internet, assinaturas).
  Em Mais → Contas recorrentes dá para editar (vale também para as contas em aberto), pausar ou excluir.
  Meses curtos usam o último dia (dia 31 em fevereiro vence dia 28/29). Meses passados nunca são criados, para não gerar falsas "atrasadas".
- **Lembrete**: o Início mostra as contas atrasadas e as que vencem nos próximos 7 dias. (Notificações do Android ficam para uma versão futura.)
- Conta paga não pode ser editada nem excluída: desfaça o pagamento antes.
- Backups feitos na versão 1 continuam funcionando (as tabelas novas são criadas ao restaurar).

### Novo na versão 3: orçamento e compras parceladas

- **Mais → Orçamento**: defina um limite mensal por categoria de despesa. Cada categoria mostra uma barra
  (verde, âmbar a partir de 80%, vermelha ao passar do limite), quanto resta ou quanto passou, e o "previsto"
  somando as contas ainda em aberto. O limite vale para todos os meses. Campo em branco ou "Remover" tira o limite.
- **Início**: cartão "Orçamento do mês" destacando as categorias em atenção ou estouradas (só aparece se houver limites).
- **Parcelar**: ao criar uma conta, informe o número de parcelas (2 a 60) e diga se o valor é de cada parcela ou o total
  (nesse caso dividimos, e os centavos que sobram vão para as primeiras). Uma conta por mês, com nome "Celular (3/10)".
  Ao pagar, o lançamento leva o mesmo nome. Na edição de uma parcela dá para excluir todas as que estão em aberto.
- Não dá para repetir todo mês e parcelar na mesma conta (escolha uma das duas).
- Ajuste visual: os avisos agora são em bloco (links no meio do texto não quebram mais o layout) e
  "Contas recorrentes" ganhou o botão "➕ Nova conta recorrente".

## Testar no computador

    pip install -r requirements.txt
    python tests/teste_basico.py     # teste automático da versão 1 (usa banco temporário)
    python tests/teste_contas.py     # teste automático das contas e recorrências
    python tests/teste_orcamento_parcelas.py   # teste automático do orçamento e das parcelas
    python app.py                    # abre em http://127.0.0.1:5000

## Gerar o APK

1. Crie um repositório PRIVADO no GitHub e envie estes arquivos (mantenha a pasta `.github`).
2. Aba **Actions → Gerar APK → Run workflow**.
3. Baixe **MEUBOLSO-APK** em "Artifacts", descompacte e instale o `app-debug.apk`.

O app tem identificador próprio (`br.com.meubolso`): instala ao lado do FINANCBETEL, sem mexer nele.
Desinstalar o Meu Bolso apaga os dados: baixe um backup antes.
Python 3.13 está definido em `android/app/build.gradle` e no workflow; se mudar um, mude o outro.

## Ideias para as próximas versões

- Cartão de crédito com fatura (fechamento e vencimento da fatura).
- Relatório em PDF.
