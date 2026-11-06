# codex-team 0.3.0

Executor local para equipes Codex com tmux, submódulos Git e filas persistentes.
Requer Python 3.10+, Git, tmux e a CLI Codex configurada, em sistema POSIX.
O pacote Python não tem dependências de runtime além da biblioteca padrão.

## Instalar a partir das fontes

```sh
python3 -m pip install .
codex-team --version
codex-team --root ./novo-time start --no-attach
```

Em instalações Python gerenciadas pelo sistema, use um venv ou pipx e mantenha
seu bin/ no PATH. Não é necessário ignorar as proteções do gerenciador Python.
`codex-team-start --root ./novo-time --no-attach` é um atalho equivalente.
O primeiro start exige uma pasta nova ou vazia. Depois use ./team na raiz criada.

## Configurações de economia incluídas

- Sessão nova por entrega; `--continue ID` retoma somente a mesma entrega.
- Esforço padrão low; modelo herdado do Codex, sem troca automática ou retry pago.
- `--context-file` fornece resumo de até 8000 caracteres sem chamada extra ao modelo.
- JSON compacto; status com tarefas ativas e contadores; result/wait compactos.
- Resultados pendentes mostram somente ID/estado; relatórios limitados e --full explícito.
- Relatórios de até 100 palavras, contratos/evidências em arquivo e tarefas agrupadas.
- Usage por delta cumulativo de sessão, separando métricas sem dupla contagem.
- Agentes ociosos aguardam em FIFO sem chamadas ao modelo.
- Terminal ocioso limpo com a frase Aguardando tarefas.; logs/resultados persistem.
- Consulta do esquema já migrado sem criar lock de escrita desnecessário.

```sh
./team config --effort low
./team task backend --file tarefa.md --context-file contexto.md
./team task backend --continue ID --message 'Correção da mesma entrega'
./team status
./team wait ID --timeout 50
./team result ID
./team usage --task ID
```

Não há economia percentual garantida: compare medições equivalentes de usage.
Cache integra entrada; raciocínio integra saída. Não some essas partes novamente.
As instruções genéricas mantêm autorizações operacionais, isolamento Node e os
checks obrigatórios de cada projeto; redução de contexto não dispensa validação.

## Times existentes

Instalar este pacote atualiza a CLI de instalação. Cada time existente usa sua
própria cópia em tools/team_executor.py, preservada pela instalação. Atualizar outra raiz exige preservar seu estado/instruções, encerrar as entregas
com ./team stop e atualizar sua cópia de forma coordenada antes de start.
O pacote contém fontes selecionadas, testes e regras genéricas; dados, sessões,
repositórios consumidores e configurações privadas não são entradas do build.

## Testes offline do pacote fonte

```sh
PYTHONPATH=src python3 -B -m unittest discover -s tests
```

Os testes usam banco temporário e eventos simulados; não chamam tmux/modelos/rede.
PROVENANCE.json identifica hashes reais da origem, do executor empacotado e das
instruções. Somente CONTROLLER_RULES foi adaptado para o template genérico atual;
o restante da AST do executor é idêntico à origem.

## Manutenção e propriedade

Este repositório mantém o executor, distribuição, testes e templates genéricos.
Cada consumidor mantém seu controlador, instruções, configuração e runtime.
Atualizações aqui não movem fontes nem alteram times em execução.
O controlador do consumidor coordena seus gitlinks e registro de responsáveis.
Nenhuma licença foi acrescentada: não havia licença própria nos fontes selecionados.

A extração revisada pode ser reproduzida sem build/release:

```sh
python3 tools/package_codex_team.py --source-root /caminho/da/origem --output /nova/pasta
```

O exportador lê apenas os dois fontes Python explicitamente selecionados;
remove a seção de canvas específica do consumidor e verifica igualdade da AST
do executor fora de CONTROLLER_RULES. PROVENANCE.json registra os hashes.
