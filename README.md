# Monitor diario de licitacoes (Opcao 1: Excel + Power Automate)

Implementacao minima para monitorar licitacoes de cal e calcario com ingestao inicial via PNCP e arquitetura preparada para novas fontes.

## Estrutura

- `config/monitor_config.json`: palavras-chave, janela de dias, destinos e fontes.
- `scripts/monitor_licitacoes.py`: ingestao, filtro, deduplicacao e consolidacao diaria.
- `data/modelo_licitacoes.csv`: modelo base (compativel com Excel).
- `docs/power-automate.md`: passo a passo do fluxo diario de e-mail no Power Automate.

## Execucao local

Pre-requisito: Python 3.10+.

Monitoramento real (online, consulta PNCP):

```powershell
python scripts\monitor_licitacoes.py --config config\monitor_config.json
```

Modo offline (sem HTTP, para validar somente o fluxo):

```powershell
python scripts\monitor_licitacoes.py --config config\monitor_config.json --skip-fetch
```

No modo offline, o CSV diario fica vazio (somente cabecalho) por design.

## Saidas geradas

- Diario: `data/daily/consolidado_YYYY-MM-DD.csv` (somente registros novos do dia, deduplicados).
- Historico acumulado: `data/consolidado_historico.csv`.
- Estado da deduplicacao: `data/estado_chaves_dedup.txt`.

Colunas padrao:

`orgao_comprador, objeto, quantidade, valor_estimado, data_abertura, link_edital, fonte, data_captura`

## Configuracao (arquivo)

No `config/monitor_config.json`, ajuste:

- `keywords`: termos monitorados.
- `window_days`: janela retroativa para busca.
- `destinations.emails`: destinatarios do e-mail diario.
- `sources.pncp.codigo_modalidade_contratacao`: modalidades PNCP consultadas.
- `sources.pncp.only_open`: quando `true`, retorna somente licitacoes abertas/em andamento.
- `sources.pncp.query_chunk_days`: divide consulta em janelas menores para reduzir timeout.
- `sources.pncp.request_retries`: tentativas por requisicao HTTP.
- `sources.pncp.max_consecutive_failures`: interrompe cedo quando a API esta indisponivel para evitar execucao longa.
- `sources.pncp.fail_on_unavailable`: quando `false`, nao quebra a execucao se PNCP estiver indisponivel (gera diario vazio com aviso).

## Agendamento diario (Windows Task Scheduler)

1. Abra **Task Scheduler** > **Create Task**.
2. Trigger: **Daily** (horario desejado).
3. Action: **Start a program**.
   - Program/script: `python`
   - Add arguments: `scripts\monitor_licitacoes.py --config config\monitor_config.json`
   - Start in: `C:\Users\tsouza\OneDrive - Carmeuse\copilot-worktrees\licitacao\tiagokaa-crispy-fortnight`
4. Salve e execute uma vez manualmente para validar geracao do CSV diario.

Depois, configure o envio de e-mail no Power Automate conforme `docs/power-automate.md`.
