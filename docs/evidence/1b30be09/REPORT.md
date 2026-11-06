# Publicação Git em master

Escopo: somente codex-team; tarefa 1b30be090c26438c8389459179310af0.

Auditoria concluída: docs/evidence/fb77abe4/REPORT.md e inventory.json.
Preservados os três ajustes revisados em MANIFEST.in, pyproject.toml e
 tools/package_codex_team.py e todas as evidências sanitizadas da auditoria.
Executor inalterado; 29 testes offline aprovados na auditoria, sem repetição
ou novo build nesta tarefa. git diff --check aprovado.

Origin SSH existente preservado: git@github.com:lauro-cesar/codex-team.git.
Consulta SSH prévia confirmou main em f9b0ce61d457302cdc4e1bae81ed3b50b4ecd583
e ausência de refs/heads/master. Branch local renomeada para master.
DNS indisponível no sandbox; leitura SSH concluída pela revisão normal.
Rename inicialmente recusado por metadados Git somente leitura no sandbox;
concluído após revisão normal, sem modificar permissões ou remover locks.

Envio autorizado: git push -u origin master, normal, sem force ou exclusões.
Recibo pós-envio com SHA, upstream, refs remotas e status será mantido em
.codex-team/1b30be09-publication.json, local e ignorado pelo Git, para evitar
commit autorreferencial. Nenhum serviço, PyPI ou outro repositório alterado.
