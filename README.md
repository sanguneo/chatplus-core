# chatplus-core

실행 중인 채팅+ Windows 클라이언트의 **로컬 캐시를 읽어 tsuba 어댑터 계약으로 답하는
읽기 전용 어댑터**입니다. tsuba와 별도 저장소이며 TUI는 없습니다. saya 같은 tsuba
클라이언트가 설정만으로 붙을 수 있도록, tsuba의 `contract/openapi.json`(0.2.5)을 그대로
답합니다. 신고하는 기능은 방 목록(`rooms.list`), `before` 페이지네이션 메시지 읽기
(`messages.read`), SSE 메시지 스트림(`messages.stream`)뿐입니다. 실제 발송(설계·합성
테스트만 존재)과 첨부 바이트 다운로드는 없습니다.

## 작동 방식

실행 중인 공식 채팅+ 클라이언트가 이미 복호화해 둔 **로컬 캐시만 읽습니다**. 프로토콜을
재구현하거나 로그인하지 않습니다.

1. **키**: Windows 자격 증명 관리자에 Flutter 보안 저장소 플러그인이 넣어 둔 16바이트 AES
   키(`key_.`)를 읽기 전용으로 가져옵니다.
2. **Hive 키**: `.secure` cipher-key 파일(AES-GCM 인증)을 그 16바이트 키로 풀면 URL-safe
   Base64 문자열이 나오고, 디코딩하면 32바이트 Hive AES 키입니다.
3. **박스**: 채팅+는 방·메시지를 Hive v2 박스 파일(`folder.hive`, `object.hive`)에 담습니다.
   각 박스는 AES-256-CBC+PKCS7로 암호화된 프레임의 추가 전용(append) 로그이며, 프레임마다
   키로 시드된 CRC32가 붙습니다(무결성 탐지용이며 변조 인증은 아닙니다).
4. **복호화**: `hive.decode_frames`가 프레임을 순서대로 재생(같은 키 교체·삭제 반영)하며
   CRC·패딩을 검증하고 값을 복호화합니다. `values.decode_value`가 Hive 어댑터 직렬화를
   레코드로 해석합니다(모르는 형식은 전체 거절).
5. **스냅샷**: `reader.snapshot()`이 두 박스를 바이트 동일 이중 읽기로 읽고, 레코드를
   tsuba의 방·메시지 모양으로 매핑합니다. 방 ID는 계정 epoch로 범위를 한정하며,
   클라이언트 창을 열거나 읽음 상태를 바꾸지 않습니다.
6. **API**: 인증된 FastAPI가 그 스냅샷을 tsuba 계약의 라우트·봉투·오류 코드·SSE 이벤트로
   답합니다. 읽기 전용이며 어떤 것도 발송하지 않습니다.

열쇠는 실행 중인 클라이언트가 그 데이터베이스를 연 동안에만 자격 증명 저장소에 있으므로,
클라이언트가 한 번도 연 적 없는 방은 읽을 수 없습니다.

## 실행

Python 3.11 이상과 uv가 필요합니다. 채팅+ PC에서 먼저 정상 로그인해야 합니다.
현재 Windows 사용자의 로컬 저장소만 읽으며 다른 계정에 로그인하지 않습니다.

```powershell
cd C:\Users\USER\WebstormProjects\chatplus-core
uv sync --frozen
$env:CHATPLUS_TOKEN = uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
uv run python -m chatplus_core
```

기본 주소는 `http://127.0.0.1:8766`입니다. 포트를 바꾸려면 `CHATPLUS_PORT`
환경 변수에 1024~65535 범위의 정수를 지정합니다(범위 밖이거나 숫자가 아니면 시작하지 않습니다).
서버는 루프백에만 바인딩하며, 32바이트 미만 토큰으로는 시작하지 않습니다.
실행 시 생성한 토큰을 클라이언트에도 설정하세요.
요청 접근 로그는 끕니다. 저장소 키와 메시지를 별도 파일로 내보내지 않습니다.

```powershell
curl.exe http://127.0.0.1:8766/health
curl.exe -H "Authorization: Bearer $env:CHATPLUS_TOKEN" http://127.0.0.1:8766/capabilities
curl.exe -H "Authorization: Bearer $env:CHATPLUS_TOKEN" http://127.0.0.1:8766/rooms
```

`/health`를 뺀 모든 라우트에 Bearer 인증이 필요하며, 토큰의 해시를 상수 시간으로 비교합니다.

| 라우트 | 결과 |
| --- | --- |
| `GET /health` | `{"ok": true}`, 토큰 없이 |
| `GET /capabilities` | 계약 0.2.5, `account.kind: user`, 기능 `rooms.list`·`messages.read`(`paging: before-id`, `maxLimit: 1000`)·`messages.stream`(`resume: cursor`) |
| `GET /rooms` | 최근 활동 순 방 목록, `complete` |
| `GET /messages?roomId=…&limit=50&before=<id>` | 오래된 것부터, `hasEarlier` |
| `GET /stream?since=<roomId>:<id>` | SSE: `ready`→`message`…→`cursor`, 조용하면 `ping`, 읽기 실패 시 `error` 후 종료 |
| `GET /media`, `POST /send`·`/edit`·`/delete`·`/react`·`/read` | 기능을 신고하지 않으므로 `501 NOT_SUPPORTED` |
| `GET /icon` | 제공자 아이콘이 없으므로 `404 NOT_FOUND` |

`limit`는 1~1000(기본 50)입니다. `before`가 없으면 가장 새로운 `limit`개를, 있으면 그 id
바로 앞의 `limit`개를 `sentAt`, `id` 오름차순으로 돌려줍니다. 이는 인과 순서나 서버 수신
순서가 아닙니다. `hasEarlier: false`면 그 방 캐시 이력의 시작이고, 그 방에 없는 `before`는
404입니다.

방과 메시지는 캐시 필드에서 이렇게 옮깁니다.

- 방 `title`은 `Name`, `kind`는 `Recipients`가 둘 이상이면 `group`, 아니면 `direct`,
  `lastActivityAt`은 방의 `Date`, `lastMessage`는 캐시에서 가장 새로운 메시지(앞 200자)입니다.
- **`unread`는 늘 0입니다.** 캐시의 읽음 상태를 아직 매핑하지 않았으므로 안 읽은 수를
  주장하지 않습니다.
- 메시지 `sender`·`senderId`는 `From`, `fromMe`는 `Direction`이 `Out`인지,
  `text`는 `TextContent`(없으면 빈 문자열)입니다. `Content-Type`이 `text/`가 아니면
  `kind: media`이며, 첨부 바이트와 `attachments`는 제공하지 않습니다(로컬 캐시가 내용을
  보관하지 않음). XML·리치 카드를 임의의 평문으로 바꾸지 않습니다.
- 해당하지 않는 키는 `null`이 아니라 아예 빠집니다.

스트림은 이름을 댄 방을 그 메시지 id 뒤부터 이어 보내고, 이름을 대지 않은 방과 스트림 도중
처음 보인 방은 지금부터 보냅니다. 형식이 틀렸거나, 같은 방을 두 번 댔거나, 캐시에 없는 방·
메시지를 가리키는 커서는 지금부터로 낮추지 않고 400으로 거절합니다. 스트림은 동시에 8개까지
받고, 아홉 번째는 `503 BUSY`입니다.

## saya 연결

saya는 이 어댑터를 tsuba 어댑터로, 설정만으로 붙입니다. 코어는 루프백에 두고 kanao처럼
`tailscale serve`로 tailnet에만 엽니다. 이 PC는 8766을 다른 프로세스가 쓰므로 8767을 씁니다.

```powershell
$env:CHATPLUS_PORT = "8767"
$env:CHATPLUS_TOKEN = (Get-Content "$HOME\.chatplus-core\token" -Raw).Trim()
uv run python -m chatplus_core
# 한 번만 하면 Tailscale이 기억합니다.
tailscale serve --bg --https=8767 http://127.0.0.1:8767
```

saya 쪽은 프록시 설정 두 가지입니다. `/adapters` 인덱스에 `{"id":"chatplus"}`를 넣고,
`/adapters/chatplus/*`를 `https://<pc>.<tailnet>.ts.net:8767`로 넘기면서
`Authorization: Bearer {env.SAYA_ADAPTER_CHATPLUS_TOKEN}`와 `Host {upstream_hostport}`를
붙입니다(saya `deploy/Caddyfile.example`의 어댑터 handle과 같은 모양). 토큰은 위 토큰 파일과
같은 값을 권한 600인 env 파일에 둡니다. 이 PC에서 코어가 꺼져 있으면 saya 목록에서 채팅+만
빠집니다.

## 보장 범위

- 로컬 캐시만 읽습니다. 휴대폰이나 서버의 전체 이력이 아닙니다.
- 방 ID는 `SHA-256(계정 epoch ‖ 방 resource URL)`입니다. 계정 간 동일 URL이 충돌하지
  않으며, 원본 URL과 epoch는 API에 노출하지 않습니다.
- Hive 프레임을 순서대로 재생하여 같은 키의 교체와 삭제를 반영합니다.
  CRC·키·길이·패딩이 맞지 않거나 지원하지 않는 값 형식이면 전체 요청을 거절합니다.
  Hive 내장 `DateTimeWithTimezone`(typeId 18)은 읽고, `null`로 저장된 키는 레코드가 없는 것으로 봅니다.
- 키 파일은 AES-GCM 인증을 확인합니다. **Hive 데이터는 AES-CBC와 CRC이며,
  CRC는 악의적인 변조에 대한 인증이 아닙니다.**
- 파일별 최대 64 MiB, 값 길이/컬렉션 원소 수 최대 1,000,000, 중첩 깊이 최대 32입니다.
  응답 개수 한도는 파일을 일부만 읽는다는 뜻이 아닙니다.
- 읽는 중 관측된 파일 변경은 거절합니다. 메시지는 방의 resource URL로 방에 넣고, 이 PC에서 보낸
  메시지처럼 같은 메시지 저장소의 다른 호스트로 방을 가리키면 경로(저장소·폴더 id)가 정확히 한 방과
  같을 때만 그 방에 넣습니다. 맞는 방이 없거나 둘 이상이면 거절합니다.
  반복 읽기 일치는 여러 파일의 원자적 스냅샷이나 ABA 방지를 증명하지 않습니다.
- 프로필 또는 대응 키가 여러 개이면 임의로 첫 항목을 고르지 않습니다.
- 읽기는 클라이언트 창을 열거나 읽음 상태를 바꾸지 않습니다.
  RCS 프로토콜, 로그인, 토큰 갱신을 재구현하지 않습니다.

모든 실패는 tsuba 봉투 `{"ok": false, "error": {"code", "message"}}`이고 코드마다 상태가
하나입니다. 잘못된 요청은 400 `INVALID_REQUEST`, 인증 실패는 401 `UNAUTHORIZED`, 없는
라우트·방·메시지는 404 `NOT_FOUND`, 다른 메서드는 405 `METHOD_NOT_ALLOWED`, 신고하지 않은
기능은 501 `NOT_SUPPORTED`, 로컬 캐시를 읽거나 풀지 못하면 500 `INTERNAL`(메시지에 경로나
계정 정보 없음), 스트림이 꽉 차면 503 `BUSY`입니다. 자동 재시도는 하지 않습니다.

## 검증

```powershell
uv run basedpyright
uv run ruff check chatplus_core tests tools
uv run ruff format --check chatplus_core tests tools
uv run python -m pytest -q
uv run python -m tools.verify_live
uv run python -m tools.verify_socket
```

합성 테스트는 실제 계정 자료를 사용하지 않습니다. 필드 이름과 값 모양만 실제 캐시에서
관측한 구조(`Recipients` 목록, `From` 문자열, `Direction` `In`/`Out`)를 따릅니다.
`tools.verify_live`는 실제 로컬 파일을 tsuba 라우트에 연결하지만 방·본문·키 대신 개수만
출력하고, 가장 큰 방을 `before`로 거꾸로 넘겨 전체 이력과 같은지 확인합니다.
`tools.verify_socket`은 비어 있는 루프백 포트를 직접 골라 `CHATPLUS_PORT`로
실제 서버를 별도 프로세스로 띄우고, 그 포트의 준비 로그를 기다린 뒤 TCP로
`/health` 200, 무인증 401, 인증 200을 확인한 다음 자신이 띄운 프로세스만 종료합니다.

2026-10-03 확인한 클라이언트는 `com.sktelecom.torchlight` 1.5.4.6입니다.
177개 합성 테스트가 통과했고, 실제 캐시에서 43개 방(모두 `direct`)·141개 메시지(본문 113개,
`media` 26개, 내가 보낸 것 15개)를 조회했습니다. 가장 큰 방(27개)은 `before` 페이지네이션이
전체 이력과 정확히 일치했고, 실제 루프백 TCP 서버로 health 200·무인증 401·인증 200·방
43개를 확인했습니다. 이 어댑터는 아무것도 보내지 않습니다. 사용자 지시로 채팅+ 창에서 114로
테스트 메시지 1건을 보냈고, 그 메시지가 다른 호스트로 방을 가리키고 Hive 내장 DateTime을 담는 것을
확인해 읽도록 했습니다. 다른 클라이언트 버전 호환성은 아직 입증하지 않았습니다.

## 계약

이 어댑터가 답하는 계약은 tsuba의 것입니다. `contract/tsuba.openapi.json`은 tsuba
`contract/openapi.json`(0.2.5)의 바이트 그대로 복사본이며, saya가 고정한 사본과 SHA-256이
같습니다. `.gitattributes`가 Windows에서 이 파일의 줄바꿈을 바꾸지 않게 합니다.

`tests/test_contract.py`는 그 사본으로 다음을 검사합니다: 바이트가 고정값과 같은지, 계약
버전, 오류 코드와 상태, 문서의 모든 라우트가 라우팅되는지(신고한 기능은 답하고, 신고하지 않은
기능은 501), `/health`를 뺀 모든 라우트가 토큰을 요구하는지, `/capabilities`·`/rooms`·
`/messages`·오류 응답·스트림 이벤트가 문서의 스키마와 맞는지. 계약을 올리려면 tsuba에서
새 바이트를 복사하고 고정값과 `CONTRACT_VERSION`을 함께 바꿉니다.

## 후속 작업

상세 의존성, 불변식, 반례, 실행 절차와 완료 조건은
[후속 작업 계획](.omo/plans/chatplus-core-followup.md)에 정리합니다. 계획의 B2(계정 신원),
F1(페이지네이션), F2(SSE), F3(발송 상태기계 설계), F4(첨부 메타데이터)와 tsuba 계약
전환(§14)은 구현됐고, OCI의 saya에 연결해 실데이터로 확인했습니다. 남은 것은 F3 실제 전송,
읽음 상태(`unread`) 매핑, 스냅샷 세대 일관성(C2)입니다.

공식 PC 지원 조건과 외부 형식 근거:

- https://chattingplus.co.kr/down
- https://chattingplus.co.kr/faq
- https://raw.githubusercontent.com/isar/hive/v2.2.3/hive/lib/src/binary/binary_reader_impl.dart
- https://raw.githubusercontent.com/isar/hive/v2.2.3/hive/lib/src/crypto/hive_aes_cipher.dart
- https://raw.githubusercontent.com/juliansteenbakker/flutter_secure_storage/develop/flutter_secure_storage_windows/windows/flutter_secure_storage_windows_plugin.cpp

공식 안내는 SK텔레콤 가입자와 One UI 6.0 이상인 SKT향/삼성 자급제 갤럭시를
PC 지원 대상으로 안내합니다. 개인 계정용 공개 API/SDK 문서는 확인하지 못했으며,
없다는 사실을 증명한 것은 아닙니다. kanao의 로컬 읽기/클라이언트 입력 분리를
참고했지만, 카카오톡의 DB·암호화 가정을 재사용하지 않았습니다.
