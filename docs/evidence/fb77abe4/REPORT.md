# Auditoria de privacidade da distribuição

Tarefa fb77abe41d2d432da57c050e25c6a72f. Resultado: fontes selecionadas atuais e
candidatos temporários sem achados nas categorias verificadas; **artefatos
históricos 0.3.0 continuam bloqueados para distribuição**. Não equivale a garantia
absoluta de ausência de informação privada. Nenhuma publicação PyPI foi feita.

## Evidências arquivo por arquivo

`inventory.json` registra tamanho, SHA-256 e achados por arquivo fonte e por CADA
membro dos quatro arquivos de distribuição inspecionados. Valores suspeitos não
são reproduzidos: apenas categoria, caminho e linha. Os hashes históricos coincidem
com o inventário já produzido pela raiz. Histórico preservado byte a byte.

| Fonte | Revisão / contrato |
| --- | --- |
| README.md | Instruções genéricas; referência consumidora histórica já removida antes desta tarefa. |
| PROVENANCE.json | Versão, caminhos relativos de origem e hashes; sem conteúdo de arquivos privados. Hashes do executor e template conferidos. Identidade da origem e AST histórica não reatestadas. |
| pyproject.toml | Nome/versão/entrypoints genéricos, sem URL/autor/credencial. Descoberta agora restrita ao pacote codex_team, sem namespaces e sem inclusão automática de package data. |
| MANIFEST.in | Antes incluía recursivamente templates Markdown e testes Python arbitrários. Agora inclui nominalmente PROVENANCE, template AGENTS e teste do executor. |
| setup.py | Apenas delega para setuptools; não consulta runtime. |
| src/codex_team/__init__.py | Versão literal. |
| src/codex_team/__main__.py | Chamada do entrypoint. |
| src/codex_team/executor.py | Referências funcionais a sessões, prompts, logs e banco de estado; não são conteúdos desses arquivos. Exemplos de repositório usam organização placeholder. Executor intacto. |
| templates/AGENTS.md | Regras genéricas de coordenação e isolamento; sem contratos do consumidor. |
| tests/test_team_executor.py | Fixtures sintéticas, banco e repositórios temporários, eventos simulados, email reservado example.invalid; sem histórico real. Único teste incluído no sdist. |
| tests/test_package_sources.py | Sentinelas sintéticas e comparação AST; teste de manutenção do checkout, fora do sdist. |
| tools/package_codex_team.py | Lê somente executor/teste de uma origem explicitamente indicada; não descobre runtime. Correção dos includes refletida no gerador. Não é scanner universal: código de entrada precisa de revisão. |
| AGENTS.md, .gitignore | Instruções/ignore do checkout, fora dos arquivos de distribuição inspecionados. Instruções locais contêm contexto de integração e não são template distribuível. |
| docs/evidence/1d82c3f4.md e recibo PUBLICATION.json | Evidência de publicação GitHub anterior; fora do pacote. Recibo privado local ignorado pelo Git, não copiado. |
| docs/evidence/fb77abe4/* | Relatório, inventário sanitizado e reprodução desta auditoria; fora do pacote. |

## Achados concretos

1. Histórico sdist: README.md:52, PKG-INFO:59 e src/codex_team.egg-info/PKG-INFO:59,
categoria consumer_reference. Histórico wheel: dist-info/METADATA:59, mesma
categoria. Não registrar o valor. Não sobrescrever, republicar nem declarar estes
arquivos liberados. A raiz mantém a decisão sobre os históricos.
2. Curingas de inclusão poderiam incorporar novos testes/templates locais.
Corrigidos neste repositório e seu gerador. Isso reduz a superfície; não protege
contra conteúdo privado inserido no próprio executor ou novos módulos Python
sob codex_team. Reinspecionar bytes de cada candidato antes de qualquer release.
3. Nenhuma chave privada, formato de credencial das famílias verificadas ou
caminho absoluto local foi encontrado nas fontes selecionadas ou nos membros dos
arquivos de distribuição. Termos token/secret em métricas e instruções não são
credenciais; nomes de runtime no código não são arquivos de runtime exportados.

## Verificações executadas

- 29 testes offline aprovados após as mudanças; nenhum executor/controlador ativo
foi alterado ou reiniciado. Testes usam subprocessos Git em diretórios temporários.
- Build offline, com setuptools 66.1.1 e wheel 0.38.4 já instalados, em cópia
selecionada e temporária; versão exclusiva 0.3.1.dev0 nessa cópia. Versão do
checkout continua 0.3.0; candidato não é uma versão aprovada para publicação.
- Oito sentinelas em teste Python adicional, template adicional, namespace extra,
package data, estado privado, sessão, credencial e documentação ficaram ausentes
de ambos os arquivos. Todos os membros foram lidos, inventariados e escaneados.
- RECORD do wheel: hashes e tamanhos conferidos. Membros absolutos, travessias e
links especiais recusados na inspeção. Nenhum segredo foi exibido.
- Busca de candidatos: submódulo inteiro, root/dist recursivo e busca adicional
até três níveis da raiz, excluindo estado/Git privados; apenas os dois históricos
foram encontrados antes dos builds desta auditoria. Não é inventário global do
host nem de outros projetos. A raiz deve fornecer quaisquer outros candidatos
fora dessa fronteira antes de considerá-los revisados.
- Logs de build ficam somente no diretório temporário indicado em inventory.json.
Não são membros DIST nem foram copiados para evidência ou distribuição. Paths
nos diagnósticos do builder não demonstram vazamento no pacote: membros reais
foram inspecionados separadamente.

## Handoff e limites

Sem bloqueio de sandbox nesta tarefa. Sem commit, push, deploy ou upload. A
publicação GitHub da tarefa anterior consta no recibo existente; esta revisão
está disponível como diff local. Sem exportar sessões/histórico/dados consumidores.

A raiz deve aplicar no seu tooling de geração os mesmos includes nominais e
restrição de descoberta de pacote; o gerador daqui já os emite. Não alterei
arquivos da raiz. A correção anterior de README da raiz permanece sob seu dono.
Uma release futura exige versão nova deliberada, geração limpa, inspeção do
wheel/sdist efetivamente escolhidos e autorização específica de publicação PyPI.

Regex cobre categorias selecionadas, não segredos arbitrários, codificados ou
informação sensível sem assinatura conhecida. A origem explicitamente passada
ao exportador pode conter dados privados em código/testes: revisão das entradas
continua necessária. Builds com outras versões do backend, caches SOURCES.txt
ou fontes diferentes exigem nova revisão de arquivos reais. O build temporário
não testou instalação via PyPI, rede ou equivalência universal entre backends.

Próximo comando de revisão: `git diff -- MANIFEST.in pyproject.toml tools/package_codex_team.py`.
Reprodução local offline: `python3 -B docs/evidence/fb77abe4/reproduce.py` (gera
outro diretório temporário e atualiza inventário desta auditoria; não publica).
