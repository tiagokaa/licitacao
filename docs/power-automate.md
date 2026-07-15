# Power Automate: envio diário por e-mail (novas licitações)

Este fluxo assume que o script Python já roda diariamente e gera:

`data/daily/consolidado_YYYY-MM-DD.csv`

Como o arquivo diário já sai deduplicado, ele representa as **novas licitações do dia**.

## 1. Preparar arquivo no OneDrive/SharePoint

1. Garanta que a pasta do projeto esteja sincronizada no OneDrive ou SharePoint.
2. Confirme que o CSV diário é gerado pelo agendamento local (Task Scheduler).

## 2. Criar fluxo no Power Automate

1. Em **Create** > **Scheduled cloud flow**.
2. Nome sugerido: `Licitacoes - Envio diario`.
3. Frequência: diária, após o horário do script (ex.: script 07:00, fluxo 07:20).

## 3. Ações do fluxo

1. **Compose** (nome: `DataLocal`):
   - Expressão:
   `formatDateTime(addHours(utcNow(), -3), 'yyyy-MM-dd')`
2. **Compose** (nome: `NomeArquivo`):
   - Expressão:
   `concat('consolidado_', outputs('DataLocal'), '.csv')`
3. **Get file content using path** (OneDrive for Business):
   - File Path:
   `/SEU_CAMINHO_NO_ONEDRIVE/data/daily/@{outputs('NomeArquivo')}`
4. **Send an email (V2)** (Outlook):
   - To: lista de e-mails definida em `config/monitor_config.json`.
   - Subject:
   `Novas licitações de cal e calcário - @{outputs('DataLocal')}`
   - Body (exemplo):
   `Segue em anexo o consolidado diário com novas licitações filtradas por palavras-chave.`
   - Attachment Name:
   `@{outputs('NomeArquivo')}`
   - Attachment Content:
   `File content` da ação anterior.

## 4. Tratamento de falha recomendado

1. Em **Get file content using path**, habilite política de repetição (retry) padrão.
2. Adicione um ramo de falha (`Configure run after`) para enviar e-mail de alerta ao time quando o arquivo diário não existir.

## 5. Operação diária

1. Task Scheduler gera o CSV diário.
2. Power Automate anexa o CSV e envia e-mail.
3. O histórico completo permanece em `data/consolidado_historico.csv`.
