# Worktrail

[English](README.md)

**AI 코딩 에이전트가 일하며 남긴 작업 기록. 다음 세션이 코드에서 추측하지 않게 한다.**

Worktrail 은 MCP 서버와 작은 로컬 창이다. Claude Code 나 Codex 가 저장소에서 일하는 동안 그 일을 **스레드**로 남긴다. 이 스레드가 무엇을 위한 것인지, 무엇을 정했고 왜 그렇게 정했는지, 아직 안 풀린 것은 무엇인지, 다음은 무엇인지. 다음 세션, 다른 에이전트, 혹은 다른 사람이건 상관 없이, diff 를 다시 읽는 대신 그 기록에서 이어받는다.

- **데모** (읽기 전용, 가상 팀의 기록): https://casebook-api.syncflo.cloud/demo/ · [영어판](https://casebook-api.syncflo.cloud/demo/en/)
- **사용 신청** (호스팅 서버): https://casebook-api.syncflo.cloud/join — 구글 로그인, 5자리, 선착순
- **피드백**: [GitHub Issues](https://github.com/jasonethicseo/worktrail/issues)

## 무엇이 기록되는가

스레드마다 네 가지를 따로 둔다. 각각을 뒤집는 것이 다르기 때문이다.

- **초점** — 이 스레드가 무엇을 위한 것이고 무엇이 되면 끝나는가.
- **결정과 제약** — 앞으로 따를 선택. 이유와 누가 정했는지(사람인지 에이전트인지)가 붙는다. 옛 결정은 사라지지 않고 대체된다.
- **증거** — 기계나 사람이 실제로 말한 것, 바이트 그대로. 테스트 결과, 에러, diff. 요약하지 않는다.
- **노트** — 에이전트가 단계마다 본 것과 결론, 그리고 **다음**: 누구 차례이고 무엇을 할 차례인가.

화면은 스레드를 사람이 아무것도 열지 않고 읽을 수 있는 세 줄로 보여 준다. 어디까지 왔는지, 다음은 무엇인지, 왜 그런지. 그 뒤의 규칙은 에이전트의 기억이 아닌, 서버가 지킨다. 주제 없이는 스레드를 못 열고, 다음 차례를 안 적으면 next 를 못 남기고, 모든 글의 첫 줄은 제목이며(120칸 이내, 기록 번호 없이), 증거는 글에 붙여 넣는 대신 번호로 단다.

## 붙는 곳

- **Claude Code** — 세션 훅이 있어 세션을 시작하면 열린 스레드가 저절로 뜬다.
- **Codex CLI** — `codex mcp add` 로 등록. 시작할 때 한 번 "casebook 열린 스레드 보여줘", "casebook 사용해서 기록 남기며 작업해줘".
- **claude.ai** — 커넥터로, OAuth 로그인 (호스팅 서버만).

**맥·리눅스·WSL** 에서 된다. 윈도우 네이티브는 아직 아니다. 설치기와 실행기가 `sh` 다.

## 설치

쉬운 길은 호스팅 서버다. https://casebook-api.syncflo.cloud/join 에서 구글로 로그인하고 데이터 안내문을 읽으면 설치 한 줄을 준다. 그 줄을 터미널에 붙여 넣으면 클라이언트 설치, 찾은 에이전트에 MCP 서버 등록, 훅 설치까지 한 번에 끝난다. 그 뒤 `casebook-ui` 가 창을 열고 `casebook-login` 이 터미널에서 로그인한다.

알아 둘 것 둘:

- MCP 서버는 **`casebook`** 이라는 이름으로 등록된다. 처음 이름이고, 이미 설치된 것을 깨지 않으려고 그대로 두었다. 에이전트에게는 casebook 이라고 부르면 된다.
- 설치할 때 기록을 어디에 둘지 묻는다. 호스팅 서버인지, 이 맥에만인지. 시험 기간에는 서버를 권한다. 고친 것이 재설치 없이 닿고, 무엇이 깨지는지 만든 사람이 볼 수 있다. 무엇을 보관하고 누가 읽을 수 있고 어떻게 지우는지는 신청 페이지에 적혀 있다.

### 소스에서 (기록은 이 맥에만)

```bash
git clone https://github.com/jasonethicseo/worktrail.git && cd worktrail
python3 -m venv .venv && .venv/bin/pip install -e ".[mcp]"
mkdir -p ~/.casebook && echo local > ~/.casebook/mode      # 서버 없이 로컬 sqlite 파일에 기록

# Claude Code
claude mcp add casebook -s user \
  -e CASEBOOK_DB=$PWD/casebook.db -e CASEBOOK_MCP_EMAIL=you@example.com \
  -- $PWD/.venv/bin/python -m casebook.adapters.mcp_proxy
sh tools/hooks/install_claude_hooks.sh          # 선택: 세션 시작·post-commit 훅

# Codex CLI 도 같다: `codex mcp add casebook --env CASEBOOK_DB=... --env CASEBOOK_MCP_EMAIL=... -- ...`

.venv/bin/python -m casebook.adapters.ui_server  # 창
```

## 구조

- `casebook/core` — 기록: 스레드·턴·증거·결정, 첫 줄 규칙.
- `casebook/adapters` — 문: `mcp_server`(stdio·HTTP), `mcp_proxy`(얇은 클라이언트), `http_api`, `ui_server`(창), `device_login`, `client_dist`(설치 한 줄이 내주는 것).
- `web/worktrail` — 화면. `web/join` — 신청 페이지.
- `tools/hooks` — Claude Code·git 훅. `tools/demo_*` — 데모의 지어낸 기록.
- `tests` — `.venv/bin/python -m pytest -q`.

## 라이선스

[Functional Source License 1.1, Apache 2.0 Future License](LICENSE) (FSL-1.1-ALv2, Sentry 가 쓰는 것). 쓰고, 복사하고, 고치고, 배포하는 것은 어떤 용도로든 된다. 안 되는 것은 이것으로 경쟁 제품이나 서비스를 출시하는 것이다. 판마다 공개 2년 뒤에는 그 판이 Apache 2.0 이 된다. 오픈소스가 아니라 source-available 이라 GitHub 에는 "Other" 로 뜬다.
