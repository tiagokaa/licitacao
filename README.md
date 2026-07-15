# Monitor diário de licitações (Opção 1: Excel + Power Automate)

Implementação mínima para monitorar licitações de **cal e calcário** com ingestão inicial via **PNCP** e arquitetura preparada para novas fontes.

## Estrutura

- `config/monitor_config.json`: palavras-chave, janela de dias, destinos e fontes.
- `scripts/monitor_licitacoes.py`: ingestão, filtro, deduplicação e consolidação diária.
- `data/modelo_licitacoes.csv`: modelo base (compatível com Excel).
- `docs/power-automate.md`: passo a passo do fluxo diário de e-mail no Power Automate.

## Execução local

Pré-requisito: Python 3.10+.

```powershell
python scripts\monitor_licitacoes.py --config config\monitor_config.json
```

Modo offline (sem chamada HTTP), útil para validar estrutura/saídas:

```powershell
python scripts\monitor_licitacoes.py --config config\monitor_config.json --skip-fetch
```

## Saídas geradas

- Diário: `data/daily/consolidado_YYYY-MM-DD.csv` (somente registros novos do dia, já deduplicados).
- Histórico acumulado: `data/consolidado_historico.csv`.
- Estado de deduplicação: `data/estado_chaves_dedup.txt`.

Colunas padrão:

`orgao_comprador, objeto, quantidade, valor_estimado, data_abertura, link_edital, fonte, data_captura`

## Agendamento diário (Windows Task Scheduler)

1. Abra **Task Scheduler** > **Create Task**.
2. Trigger: **Daily** (horário desejado).
3. Action: **Start a program**.
   - Program/script: `python`
   - Add arguments: `scripts\monitor_licitacoes.py --config config\monitor_config.json`
   - Start in: `C:\Users\tsouza\OneDrive - Carmeuse\copilot-worktrees\licitacao\tiagokaa-crispy-fortnight`
4. Salve e execute uma vez manualmente para validar geração do CSV diário.

Depois, configure o fluxo do Power Automate conforme `docs/power-automate.md`.

## Configuração (arquivo)

No `config/monitor_config.json`, ajuste:

- `keywords`: termos monitorados.
- `window_days`: janela retroativa para busca.
- `destinations.emails`: destinatários do e-mail diário.
- `sources.pncp.codigo_modalidade_contratacao`: modalidades PNCP consultadas.
